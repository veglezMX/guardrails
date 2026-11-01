"""Literal controls run only with synthetic socket/TLS/thread/file dependencies."""
from __future__ import annotations

import builtins
import hashlib
import stat
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from guardrails.evolution.classifier_contract import canonical, identity

from mvp.tests.evolution.e2e import classifier_cni_controls as subject
from mvp.tests.evolution.unit.test_classifier_cni_evidence import fixture


def literal(source, modules):
    def imports(name, *args, **kwargs):
        if name in modules: return modules[name]
        assert name in {"hashlib", "ipaddress", "json", "re", "stat", "struct", "sys"}, name
        return builtins.__import__(name, *args, **kwargs)
    safe = {**vars(builtins), "__import__": imports}
    def execute(code, namespace):
        namespace.setdefault("__builtins__", safe)
        builtins.exec(code, namespace)  # noqa: S102 - fixed in-repository source with synthetic imports
    safe["exec"] = execute
    namespace = {"__name__": "synthetic_control", "__builtins__": safe}
    execute(compile(source, "synthetic-control", "exec"), namespace)
    return namespace


def dns_program(transport, fault):
    events = []
    settings = {"source_ip": "10.244.0.15", "server_ip": "10.96.0.10", "resolved_ip": "10.96.0.1", "transport": transport}
    question = b"".join(bytes([len(p)]) + p.encode() for p in ("kubernetes", "default", "svc", "cluster", "local")) + b"\x00\x00\x01\x00\x01"
    flags = {"opcode": 0x8980, "truncated": 0x8380, "rcode": 0x8183, "not_response": 0x0180}.get(fault, 0x8180)
    header = struct.pack("!6H", 0x1234 if fault == "identifier" else 0x4752, flags, 1, 2 if fault == "answers" else 1, 0, 1 if fault == "additional" else 0)
    name = question[:-4] if fault == "uncompressed" else b"\xc0\x10" if fault == "pointer" else b"\xc0\x0c"
    packet = header + question + name + struct.pack("!HHIH", 28 if fault == "kind" else 1, 2 if fault == "class" else 1, 5, 4) + (b"\x01\x01\x01\x01" if fault == "answer_ip" else b"\x0a\x60\x00\x01")
    if fault == "question": packet = packet[:13] + b"X" + packet[14:]
    if fault == "trailing": packet += b"x"
    pending = bytearray(struct.pack("!H", 4097 if fault == "oversized" else len(packet)) + packet)
    class Channel:
        def bind(self, address): events.append(("bind", address)); self.source = address
        def settimeout(self, seconds): assert seconds == 2
        def connect(self, address):
            events.append(("connect", address))
            if fault == "refused": raise ConnectionRefusedError()
        def sendall(self, value): events.append(("wire", value))
        def sendto(self, value, address): events.append(("wire", value, address)); return 0 if fault == "short_send" else len(value)
        def recv(self, length):
            if fault == "timeout": raise TimeoutError()
            if fault == "eof": return b""
            count = min(length, 3)
            data = bytes(pending[:count]); del pending[:count]; return data
        def recvfrom(self, maximum):
            assert maximum == 4097
            if fault == "timeout": raise TimeoutError()
            return packet if fault != "oversized" else b"x" * 4097, ("10.96.0.99" if fault == "wrong_peer" else settings["server_ip"], 53)
        def getsockname(self): return ("10.244.0.99", 12345) if fault == "wrong_source" else self.source
        def close(self): events.append(("close",))
    tick = [0]
    def clock(): tick[0] += 1; return 100 if tick[0] == 1 else 104 if fault == "deadline" else 100.5
    modules = {"socket": SimpleNamespace(socket=lambda *_: Channel(), AF_INET=2, SOCK_STREAM=1, SOCK_DGRAM=2),
        "signal": SimpleNamespace(), "time": SimpleNamespace(monotonic=clock)}
    return literal(subject.DNS_SCRIPT, modules)["perform"], settings, events


@pytest.mark.parametrize("transport", ("tcp", "udp"))
@pytest.mark.parametrize("fault", (None, "uncompressed", "identifier", "opcode", "truncated", "rcode", "not_response", "answers", "additional", "pointer", "kind", "class", "answer_ip", "question", "trailing", "oversized", "timeout", "wrong_source", "deadline"))
def test_dns_control_binds_original_source_direct_server_and_exact_wire_answer(transport, fault):
    perform, settings, events = dns_program(transport, fault)
    if fault in (None, "uncompressed"): assert perform(settings) == settings
    else:
        with pytest.raises((ValueError, OSError)): perform(settings)
    assert events[0] == ("bind", (settings["source_ip"], 0)) and events[-1] == ("close",)
    wire = next(e[1] for e in events if e[0] == "wire")
    assert b"Authorization" not in wire and b"kubernetes" in wire


@pytest.mark.parametrize(("transport", "fault"), (("tcp", "refused"), ("tcp", "eof"), ("udp", "short_send"), ("udp", "wrong_peer")))
def test_dns_transport_failures_are_not_positive_control_success(transport, fault):
    perform, settings, _ = dns_program(transport, fault)
    with pytest.raises((ValueError, OSError)): perform(settings)


@pytest.mark.parametrize("fault", ("extra", "source_loopback", "server_external", "answer_linklocal", "unknown_transport"))
def test_dns_invalid_settings_refuse_before_socket_activity(fault):
    perform, settings, events = dns_program("udp", None)
    if fault == "extra": settings["token"] = "must-never-be-sent"
    elif fault == "source_loopback": settings["source_ip"] = "127.0.0.1"
    elif fault == "server_external": settings["server_ip"] = "1.1.1.1"
    elif fault == "answer_linklocal": settings["resolved_ip"] = "169.254.169.254"
    else: settings["transport"] = "raw"
    with pytest.raises(ValueError): perform(settings)
    assert not events


def sink_program(fault):
    events = []
    body = {"transport": "tls", "counts": {"8443": 11, "9443": 0, "8445": 0}}
    if fault == "transport": body["transport"] = "plaintext"
    elif fault == "missing_count": del body["counts"]["8445"]
    elif fault == "bool_count": body["counts"]["8443"] = True
    elif fault == "negative_count": body["counts"]["8443"] = -1
    raw = canonical(body)
    if fault == "duplicate_json": raw = b'{"transport":"tls","transport":"tls","counts":{"8443":11,"9443":0,"8445":0}}'
    headers = [("Content-Length", str(len(raw)))]
    if fault == "duplicate_length": headers.append(headers[0])
    elif fault == "chunked": headers.append(("Transfer-Encoding", "chunked"))
    elif fault == "compressed": headers.append(("Content-Encoding", "gzip"))
    elif fault == "length": headers[0] = ("Content-Length", "4097")
    elif fault == "headers": headers += [("X-Filler", "x")] * 64
    class Channel:
        def bind(self, address): self.source = address; events.append(("bind", address))
        def settimeout(self, seconds): assert seconds == 2
        def connect(self, address): events.append(("connect", address))
        def getsockname(self): return ("10.244.0.99", 12345) if fault == "wrong_source" else self.source
        def version(self): return "TLSv1" if fault == "tls" else "TLSv1.3"
        def sendall(self, value): events.append(("wire", value))
        def close(self): events.append(("close",))
    class Context:
        def __init__(self, mode): assert mode == 2
        def load_verify_locations(self, **kwargs): assert kwargs == {"cadata": "synthetic-ca"}
        def wrap_socket(self, channel, **kwargs):
            assert self.minimum_version == 12 and kwargs == {"server_hostname": "probe.gr-platform.svc.cluster.local"}
            if fault == "certificate": raise OSError("synthetic certificate failure")
            return channel
    class Response:
        status = 500 if fault == "status" else 200
        def __init__(self, channel): assert isinstance(channel, Channel)
        def begin(self): pass
        def getheaders(self): return headers
        def read(self, maximum): assert maximum == 4097; return raw[:-1] if fault == "truncated" else raw
    tick = [0]
    def clock(): tick[0] += 1; return 100 if tick[0] == 1 else 105 if fault == "deadline" else 100.1
    modules = {"socket": SimpleNamespace(socket=lambda *_: Channel(), AF_INET=2, SOCK_STREAM=1),
        "ssl": SimpleNamespace(SSLContext=Context, PROTOCOL_TLS_CLIENT=2, TLSVersion=SimpleNamespace(TLSv1_2=12)),
        "http.client": SimpleNamespace(client=SimpleNamespace(HTTPResponse=Response)), "os": SimpleNamespace(),
        "signal": SimpleNamespace(), "time": SimpleNamespace(monotonic=clock)}
    namespace = literal(subject.SINK_SCRIPT, modules)
    def private(path, maximum, digest):
        assert (path, maximum, digest) == ("/secrets/ca.crt", 65536, "a" * 64)
        if fault == "ca": raise ValueError()
        return b"synthetic-ca"
    namespace["read"] = private
    return namespace["perform"], events


@pytest.mark.parametrize("positive", (False, True))
@pytest.mark.parametrize("fault", (None, "ca", "certificate", "tls", "wrong_source", "status", "truncated", "duplicate_length", "chunked", "compressed", "length", "headers", "transport", "missing_count", "bool_count", "negative_count", "duplicate_json", "deadline"))
def test_sink_counter_control_requires_pinned_ca_original_source_tls_and_finite_unambiguous_body(positive, fault):
    perform, events = sink_program(fault)
    settings = {"source_ip": "10.244.0.15", "destination_ip": "10.96.0.21", "ca_sha256": "a" * 64, "positive": positive}
    if fault:
        with pytest.raises((ValueError, OSError)): perform(settings)
    else:
        assert perform(settings) == {"counts": {"8443": 11, "9443": 0, "8445": 0}, "tls": "TLSv1.3"}
        wire = next(e[1] for e in events if e[0] == "wire")
        assert wire.startswith(b"GET / HTTP" if positive else b"GET /count HTTP")
        assert b"Authorization" not in wire and b"Bearer" not in wire
    if fault != "ca": assert events[-1] == ("close",)


def api_fixture(monkeypatch, fault=None):
    bound, calls, checked = fixture(), [], []
    state = Path("/synthetic")
    observer = SimpleNamespace(bound={"files": {"assembly/private/classifier.token": "a" * 64, "assembly/tls/gr-platform/classifier/ca.crt": "b" * 64}},
        deployment=SimpleNamespace(state=state, kubeconfig=state / "kubeconfig", bin=state / "bin"),
        _api_pins={"kubeconfig": hashlib.sha256(b"static").hexdigest()}, _api_private={"kubeconfig"}, _api_owner={}, _api_images={},
        _api_server="https://127.0.0.1:12345", _native=lambda *_: "https://127.0.0.1:" + ("54321" if fault == "route" else "12345"), _api_input="static")
    def recheck(**kwargs):
        checked.append(kwargs)
        return {} if fault == "final" and len(checked) > 1 else bound
    binder = SimpleNamespace(observer=observer, bound=bound, closure={"sink_ca_sha256": "c" * 64}, recheck_locked=recheck)
    monkeypatch.setattr(subject, "private_bytes", lambda *_args, **_kwargs: b"changed" if fault == "credentials" else b"static")
    def command(args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs == {"timeout": 12, "maximum": 4096, "input_text": "static"}
        return canonical({"synthetic": True}).decode()
    observer._command = command
    return subject.CniControls(binder), calls, checked


@pytest.mark.parametrize("program", (subject.READY_SCRIPT, subject.DNS_SCRIPT, subject.SINK_SCRIPT))
@pytest.mark.parametrize("fault", (None, "route", "credentials", "final", "program", "origin"))
def test_control_exec_uses_static_owned_api_and_rechecks_exact_executing_and_target_processes(monkeypatch, program, fault):
    reader, calls, checked = api_fixture(monkeypatch, fault)
    origin = "runtime-1" if program == subject.READY_SCRIPT else "allowed-team-a"
    if fault == "origin": origin = "classifier"
    selected = "import unreviewed" if fault == "program" else program
    if fault:
        with pytest.raises(RuntimeError): reader._exec(origin, selected, {}, deadline=500)
        assert len(calls) == (1 if fault == "final" else 0)
    else:
        assert reader._exec(origin, program, {}, deadline=500) == {"synthetic": True}
        origins = (origin,) if program == subject.DNS_SCRIPT else (origin, "classifier" if program == subject.READY_SCRIPT else "sink")
        assert checked == [{"deadline": 500, "origins": origins}] * 2
        args = calls[0][0]
        assert args[:5] == [Path("/synthetic/bin/kubectl"), "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution"]
        assert args[-5:] == ["python", "-B", "-c", program, "{}"]


@pytest.mark.parametrize("fault", (None, "bool_status", "tls", "identity", "epoch", "negative_counter", "bool_counter", "nan", "infinite", "too_late", "extra"))
def test_authenticated_readiness_control_retains_actual_epoch_and_zero_dispatch_baseline(monkeypatch, fault):
    reader, _, _ = api_fixture(monkeypatch)
    value = {"status": 200, "tls": "TLSv1.3", "identity_sha256": hashlib.sha256(canonical(identity())).hexdigest(), "epoch": "d" * 32, "attempts": 12, "elapsed_ms": 50}
    if fault == "bool_status": value["status"] = True
    elif fault == "tls": value["tls"] = "TLSv1"
    elif fault == "identity": value["identity_sha256"] = "f" * 64
    elif fault == "epoch": value["epoch"] = "foreign"
    elif fault == "negative_counter": value["attempts"] = -1
    elif fault == "bool_counter": value["attempts"] = True
    elif fault == "nan": value["elapsed_ms"] = float("nan")
    elif fault == "infinite": value["elapsed_ms"] = float("inf")
    elif fault == "too_late": value["elapsed_ms"] = 8000
    elif fault == "extra": value["unbound"] = True
    def execute(origin, program, settings, **kwargs):
        assert origin == "runtime-2" and program == subject.READY_SCRIPT and kwargs == {"deadline": 500}
        assert settings["address"] == "10.96.0.20" and settings["token_sha256"] == "a" * 64
        return value
    monkeypatch.setattr(reader, "_exec", execute)
    if fault:
        with pytest.raises((ValueError, RuntimeError)): reader.ready("runtime-2", deadline=500)
    else: assert reader.ready("runtime-2", deadline=500)["counter"] == {"epoch": "d" * 32, "attempts": 12}


def host_fixture(monkeypatch, fault=None):
    events, signals, sockets, workers = [], [], [], []
    host = subject.HostSink()
    class Event:
        def __init__(self): self.set_value = False; signals.append(self)
        def set(self): self.set_value = True
        def is_set(self): return self.set_value
        def wait(self, timeout): assert 0 <= timeout <= 1; return self.set_value and fault not in {"start", "start_cleanup"}
    class Condition:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def notify_all(self): events.append("notified")
        def wait_for(self, predicate, timeout): assert 0 <= timeout <= 2; return predicate()
    class Worker:
        def __init__(self, *, target, name, daemon):
            assert name == "classifier-cni-host-sink" and daemon is True
            self.target, self.alive = target, False; workers.append(self)
        def start(self): self.alive = True; signals[1].set()
        def is_alive(self): return self.alive
        def join(self, *, timeout): assert timeout == 2; self.alive = fault in {"cleanup", "start_cleanup"}
    class Channel:
        def __init__(self): self.address = None; sockets.append(self)
        def bind(self, address):
            assert address == ("192.168.51.1", 0)
            if fault in {"bind", "bind_cleanup"}: raise OSError()
            self.address = (address[0], 80 if fault == "privileged" else 12345)
        def listen(self, backlog): assert backlog == 16
        def settimeout(self, seconds): assert 0 < seconds <= 2
        def getsockname(self): return self.address
        def fileno(self): return 99
        def connect(self, address):
            assert self.address[0] == "192.168.51.1" and address == ("192.168.51.1", 12345)
            if fault == "connect": raise OSError()
            if fault != "missing_accept": host.accepted += 2 if fault == "extra_accept" else 1
        def close(self):
            events.append("socket_closed")
            if fault == "bind_cleanup": raise OSError()
    fake_socket = SimpleNamespace(socket=lambda *_: Channel(), AF_INET=2, SOCK_STREAM=1)
    fake_thread = SimpleNamespace(Condition=Condition, Event=Event, Thread=Worker)
    imports = builtins.__import__
    def synthetic_import(name, *args, **kwargs):
        return {"socket": fake_socket, "threading": fake_thread}.get(name) or imports(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", synthetic_import)
    native = SimpleNamespace(st_mode=stat.S_IFREG if fault == "not_socket" else stat.S_IFSOCK, st_dev=1, st_ino=2)
    monkeypatch.setattr(subject, "os", SimpleNamespace(fstat=lambda _: native))
    process = {"pid": 123, "start_ticks": 5000, "netns": "net:[2000]"}
    monkeypatch.setattr(subject, "_process", lambda: dict(process))
    now = [100]
    monkeypatch.setattr(subject.time, "monotonic", lambda: now[0])
    return host, events, signals, sockets, workers, native, process, now


@pytest.mark.parametrize("fault", (None, "bind", "not_socket", "privileged", "start", "bind_cleanup", "start_cleanup"))
def test_host_sink_binds_only_approved_bridge_owns_socket_and_process_and_closes_failed_setup(monkeypatch, fault):
    host, events, _signals, _sockets, workers, *_ = host_fixture(monkeypatch, fault)
    if fault:
        with pytest.raises(RuntimeError, match="classifier_cni_host_sink_unavailable") as caught: host.open_locked(deadline=220)
        assert events.count("socket_closed") == 1
        if fault in {"bind_cleanup", "start_cleanup"}:
            assert caught.value.__notes__ == ["CNI host sink setup cleanup also failed: " + ("OSError" if fault == "bind_cleanup" else "ClassifierObservationError")]
    else:
        bound = host.open_locked(deadline=220)
        assert bound == {"address": "192.168.51.1", "port": 12345, "identity_sha256": hashlib.sha256(canonical(host.descriptor)).hexdigest()}
        assert host.positive() == 1 and host.count() == 1
        host.close()
        assert not workers[0].is_alive() and events.count("socket_closed") == 2


@pytest.mark.parametrize("fault", ("worker", "failed", "stopped", "deadline", "device", "inode", "process", "address"))
def test_host_sink_original_identity_cannot_be_replaced_or_reused_after_deadline(monkeypatch, fault):
    host, _events, signals, sockets, workers, native, process, now = host_fixture(monkeypatch)
    host.open_locked(deadline=220)
    if fault == "worker": workers[0].alive = False
    elif fault == "failed": host.failed = True
    elif fault == "stopped": signals[0].set()
    elif fault == "deadline": now[0] = 220
    elif fault == "device": native.st_dev += 1
    elif fault == "inode": native.st_ino += 1
    elif fault == "process": process["start_ticks"] += 1
    else: sockets[0].address = ("192.168.51.1", 54321)
    with pytest.raises(RuntimeError): host.identity()


@pytest.mark.parametrize("fault", ("connect", "missing_accept", "extra_accept", "cleanup"))
def test_host_control_refuses_unproven_delivery_and_cannot_hide_live_listener_after_cleanup(monkeypatch, fault):
    host, *_ = host_fixture(monkeypatch, fault)
    host.open_locked(deadline=220)
    with pytest.raises((OSError, RuntimeError)):
        host.close() if fault == "cleanup" else host.positive()


@pytest.mark.parametrize("fault", ("accept_error", "overflow"))
def test_host_receive_worker_counts_only_accepted_connections_and_stops_on_error_or_finite_limit(monkeypatch, fault):
    host, _events, _signals, sockets, workers, *_ = host_fixture(monkeypatch)
    host.open_locked(deadline=220)
    sequence = iter(["timeout", "accepted", "error"])
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def accept():
        value = next(sequence)
        if value == "timeout": raise TimeoutError()
        if value == "error": raise OSError()
        return Connection(), ("192.168.51.1", 54321)
    sockets[0].accept = accept
    if fault == "overflow": host.accepted = 64
    workers[0].target()
    assert host.failed is True and host.accepted == (65 if fault == "overflow" else 1)
    with pytest.raises(RuntimeError): host.count()


@pytest.mark.parametrize("fault", (None, "oversized", "wrong_pid", "missing_start", "bad_namespace"))
def test_parent_process_fingerprint_uses_bounded_nofollow_stat_and_native_network_namespace(monkeypatch, fault):
    calls = []
    raw = ("999" if fault == "wrong_pid" else "123") + " (cni parent) S " + " ".join(["0"] * 18 + (["5000"] if fault != "missing_start" else []))
    if fault == "oversized": raw = "x" * 4097
    def opened(path, flags):
        assert path == "/proc/123/stat" and flags == 7
        calls.append("open"); return 99
    fake = SimpleNamespace(getpid=lambda: 123, open=opened, O_RDONLY=1, O_NOFOLLOW=2, O_NONBLOCK=4,
        read=lambda fd, maximum: raw.encode() if fd == 99 and maximum == 4097 else pytest.fail("unexpected read"),
        close=lambda fd: calls.append(("close", fd)), readlink=lambda path: "mnt:[1]" if fault == "bad_namespace" else "net:[2000]")
    monkeypatch.setattr(subject, "os", fake)
    if fault:
        with pytest.raises(RuntimeError): subject._process()
    else: assert subject._process() == {"pid": 123, "start_ticks": 5000, "netns": "net:[2000]"}
    assert calls == ["open", ("close", 99)]
