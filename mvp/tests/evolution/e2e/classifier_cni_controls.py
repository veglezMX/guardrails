"""Original bounded host/TLS-sink/DNS/readiness controls for CNI observations.

No listener, thread, file read or network import occurs until an explicit
operation. The parent owns launch provenance and the exclusive deployment lock.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time

from evolution.classifier_dispatch import snapshot
from guardrails.evolution.classifier_contract import canonical, identity, parse

from mvp.tests.evolution.e2e.classifier_network_support import (
    READY_SCRIPT,
    private_bytes,
    require,
)


def _process():
    pid = os.getpid()
    fd = os.open("/proc/" + str(pid) + "/stat", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        raw = os.read(fd, 4097).decode("ascii")
    finally:
        os.close(fd)
    require(len(raw) <= 4096)
    match = re.fullmatch(r"([1-9][0-9]*) \((.*)\) ([A-Za-z]) (.*)\n?", raw)
    require(match is not None and int(match[1]) == pid)
    fields = (match[3] + " " + match[4]).split()
    require(len(fields) >= 20 and re.fullmatch(r"[1-9][0-9]*", fields[19]) is not None)
    netns = os.readlink("/proc/" + str(pid) + "/ns/net")
    require(re.fullmatch(r"net:\[[1-9][0-9]*\]", netns) is not None)
    return {"pid": pid, "start_ticks": int(fields[19]), "netns": netns}


class HostSink:
    """Owned count-only TCP sink on the already approved native bridge address."""
    def __init__(self):
        self.listener = self.worker = None
        self.failed = False
        self.accepted = 0

    def open_locked(self, *, deadline):
        import socket
        import threading

        require(self.listener is None and deadline > time.monotonic())
        self.socket, self.deadline = socket, deadline
        self.condition, self.stop, self.started = threading.Condition(), threading.Event(), threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(("192.168.51.1", 0))
            self.listener.listen(16)
            self.listener.settimeout(0.1)
            self.address = self.listener.getsockname()
            info = os.fstat(self.listener.fileno())
            require(stat.S_ISSOCK(info.st_mode) and 1024 <= self.address[1] <= 65535)
            self.descriptor = {"process": _process(), "address": self.address[0], "port": self.address[1],
                "device": info.st_dev, "inode": info.st_ino}
            def receive():
                self.started.set()
                while not self.stop.is_set():
                    try:
                        connection, _ = self.listener.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        if not self.stop.is_set():
                            with self.condition:
                                self.failed = True
                                self.condition.notify_all()
                        return
                    with connection, self.condition:
                        self.accepted += 1
                        if self.accepted > 64:
                            self.failed = True
                            self.stop.set()
                        self.condition.notify_all()
            self.worker = threading.Thread(target=receive, name="classifier-cni-host-sink", daemon=True)
            self.worker.start()
            require(self.started.wait(min(1, max(0, deadline - time.monotonic()))))
            return self.identity()
        except Exception:  # noqa: BLE001 - infrastructure diagnostics remain finite and private
            primary = RuntimeError("classifier_cni_host_sink_unavailable")
            try:
                self.close()
            except BaseException as cleanup_error:  # noqa: BLE001 - preserve the finite setup refusal and cleanup failure
                primary.add_note("CNI host sink setup cleanup also failed: " + type(cleanup_error).__name__)
            raise primary from None

    def identity(self):
        require(self.listener is not None and self.worker is not None and self.worker.is_alive()
                and not self.failed and not self.stop.is_set() and time.monotonic() < self.deadline)
        info = os.fstat(self.listener.fileno())
        require(stat.S_ISSOCK(info.st_mode) and info.st_dev == self.descriptor["device"] and info.st_ino == self.descriptor["inode"]
                and _process() == self.descriptor["process"] and self.listener.getsockname() == self.address)
        return {"address": self.address[0], "port": self.address[1], "identity_sha256": hashlib.sha256(canonical(self.descriptor)).hexdigest()}

    def count(self):
        self.identity()
        with self.condition:
            require(not self.failed and 0 <= self.accepted <= 64)
            return self.accepted

    def positive(self):
        before = self.count()
        connection = self.socket.socket(self.socket.AF_INET, self.socket.SOCK_STREAM)
        try:
            connection.bind(("192.168.51.1", 0))
            connection.settimeout(min(2, max(0.001, self.deadline - time.monotonic())))
            connection.connect(self.address)
        finally:
            connection.close()
        with self.condition:
            require(self.condition.wait_for(lambda: self.accepted != before or self.failed,
                timeout=min(2, max(0, self.deadline - time.monotonic()))) and not self.failed and self.accepted == before + 1)
        return self.count()

    def close(self):
        if self.listener is None:
            return
        self.stop.set()
        self.listener.close()
        if self.worker is not None:
            self.worker.join(timeout=2)
            require(not self.worker.is_alive())


DNS_SCRIPT = r'''
import ipaddress,json,signal,socket,struct,sys,time
def need(value):
    if not value: raise ValueError('classifier_cni_dns_control_invalid')
def perform(settings):
    need(type(settings) is dict and set(settings) == {'source_ip','server_ip','resolved_ip','transport'})
    for key in ('source_ip','server_ip','resolved_ip'):
        ip = ipaddress.IPv4Address(settings[key])
        need(str(ip) == settings[key] and ip.is_private and not (ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local))
    need(settings['transport'] in ('udp','tcp'))
    question = b''.join(bytes([len(p)])+p.encode('ascii') for p in ('kubernetes','default','svc','cluster','local')) + b'\x00\x00\x01\x00\x01'
    query = b'\x47\x52\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'+question
    stream = settings['transport'] == 'tcp'
    channel = socket.socket(socket.AF_INET,socket.SOCK_STREAM if stream else socket.SOCK_DGRAM)
    start = time.monotonic()
    try:
        channel.bind((settings['source_ip'],0)); channel.settimeout(2)
        if stream:
            channel.connect((settings['server_ip'],53));channel.sendall(struct.pack('!H',len(query))+query)
            def read(size):
                result = b''
                while len(result) < size:
                    need(time.monotonic()-start < 3)
                    chunk = channel.recv(size-len(result));need(bool(chunk));result += chunk
                return result
            size = struct.unpack('!H',read(2))[0];need(12 <= size <= 4096);raw = read(size)
        else:
            need(channel.sendto(query,(settings['server_ip'],53)) == len(query))
            raw,address = channel.recvfrom(4097);need(address[:2] == (settings['server_ip'],53) and len(raw) <= 4096)
        need(channel.getsockname()[0] == settings['source_ip'] and time.monotonic()-start < 3 and len(raw) >= 12)
        identifier,flags,questions,answers,authority,additional = struct.unpack('!6H',raw[:12])
        need(identifier == 0x4752 and flags & 0x8000 and not flags & 0x7a00 and flags & 0x000f == 0
             and questions == 1 and answers == 1 and authority == 0 and additional == 0 and raw[12:12+len(question)] == question)
        offset = 12+len(question)
        # The one fixed question is echoed; only its exact name or a pointer to
        # that question may identify the answer. No arbitrary DNS name walker.
        if raw[offset:offset+2] == b'\xc0\x0c': offset += 2
        else:
            name = question[:-4];need(raw[offset:offset+len(name)] == name);offset += len(name)
        need(len(raw) >= offset+10)
        kind,klass,ttl,length = struct.unpack('!HHIH',raw[offset:offset+10]);offset += 10
        need(kind == klass == 1 and length == 4 and len(raw) == offset+4
             and str(ipaddress.IPv4Address(raw[offset:])) == settings['resolved_ip'])
        return settings
    finally: channel.close()
if __name__ == '__main__':
    signal.alarm(5)
    try:
        need(len(sys.argv) == 2 and len(sys.argv[1]) <= 4096)
        settings=json.loads(sys.argv[1]);print(json.dumps(perform(settings),sort_keys=True,separators=(',',':')))
    except Exception:
        print('{"status":"failed"}');raise SystemExit(1) from None
'''


SINK_SCRIPT = "READY_SOURCE = " + repr(READY_SCRIPT) + "\n" + r'''
import hashlib,http.client,ipaddress,json,signal,socket,ssl,sys,time
library={'__name__':'classifier_readiness_library'}
exec(compile(READY_SOURCE,'classifier-readiness-library','exec'),library)
need,read=library['need'],library['read']
def perform(settings):
    need(type(settings) is dict and set(settings) == {'source_ip','destination_ip','ca_sha256','positive'})
    need(type(settings['positive']) is bool)
    for key in ('source_ip','destination_ip'):
        address=ipaddress.IPv4Address(settings[key]);need(str(address)==settings[key] and address.is_private
            and not(address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local))
    ca=read('/secrets/ca.crt',65536,settings['ca_sha256'])
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=ca.decode('ascii'))
    raw=socket.socket(socket.AF_INET,socket.SOCK_STREAM);channel=raw;start=time.monotonic()
    try:
        raw.bind((settings['source_ip'],0));raw.settimeout(2);raw.connect((settings['destination_ip'],8443))
        channel=context.wrap_socket(raw,server_hostname='probe.gr-platform.svc.cluster.local')
        need(channel.version() in ('TLSv1.2','TLSv1.3') and channel.getsockname()[0] == settings['source_ip'])
        channel.sendall(('GET '+('/' if settings['positive'] else '/count')+' HTTP/1.1\r\nHost: probe.gr-platform.svc.cluster.local\r\nConnection: close\r\n\r\n').encode())
        response=http.client.HTTPResponse(channel);response.begin();headers=response.getheaders()
        need(len(headers)<=64)
        selected={}
        for key,value in headers:
            need(len(key)<=128 and len(value)<=4096)
            key=key.lower()
            if key in ('content-length','content-encoding','transfer-encoding'):
                need(key not in selected);selected[key]=value
        need(selected.get('content-length','').isdigit() and 0<int(selected['content-length'])<=4096
             and selected.get('content-encoding','identity')=='identity' and 'transfer-encoding' not in selected)
        body=response.read(4097);need(len(body)==int(selected['content-length']) and response.status==200)
        value=library['load'](body)
        need(type(value) is dict and set(value)=={'transport','counts'} and value['transport']=='tls'
             and type(value['counts']) is dict and set(value['counts'])=={'8443','9443','8445'}
             and all(type(count) is int and 0<=count<=2**63-1 for count in value['counts'].values())
             and time.monotonic()-start<4)
        return {'counts':value['counts'],'tls':channel.version()}
    finally:
        channel.close();raw.close()
if __name__=='__main__':
    signal.alarm(5)
    try:
        need(len(sys.argv)==2 and len(sys.argv[1])<=4096)
        print(json.dumps(perform(library['load'](sys.argv[1].encode())),sort_keys=True,separators=(',',':')))
    except Exception:
        print('{"status":"failed"}');raise SystemExit(1) from None
'''


class CniControls:
    def __init__(self, binding_reader):
        self.binding_reader = binding_reader
        self.observer = binding_reader.observer

    def _exec(self, origin, program, settings, *, deadline):
        require(program in (READY_SCRIPT, DNS_SCRIPT, SINK_SCRIPT))
        require((program == READY_SCRIPT and origin in {"runtime-1", "runtime-2"})
                or (program in (DNS_SCRIPT, SINK_SCRIPT) and origin == "allowed-team-a"))
        observer, d = self.observer, self.observer.deployment
        origins = (origin,) if program == DNS_SCRIPT else (origin, "sink" if program == SINK_SCRIPT else "classifier")
        bound = self.binding_reader.recheck_locked(deadline=deadline, origins=origins)
        peer = bound["peers"][origin]
        require(d.kubeconfig == d.state / "kubeconfig")
        for name, digest in observer._api_pins.items():
            raw = private_bytes(d.state / name, deadline=deadline,
                modes=(0o600,) if name in observer._api_private else (0o600, 0o644))
            require(hashlib.sha256(raw).hexdigest() == digest)
        require(observer._native(observer._api_owner, observer._api_images) == observer._api_server)
        raw = observer._command([d.bin / "kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution",
            "--namespace", peer["namespace"], "--request-timeout=10s", "exec", peer["pod_name"], "-c", peer["container"],
            "--", "python", "-B", "-c", program, canonical(settings).decode()], timeout=12, maximum=4096, input_text=observer._api_input)
        require(self.binding_reader.recheck_locked(deadline=deadline, origins=origins) == bound)
        return parse(raw.encode(), limit=4096)

    def ready(self, runtime, *, deadline):
        bound = self.binding_reader.bound
        before = self.observer.bound
        settings = {"address": bound["addresses"]["classifier_service"], "identity": identity(),
            "token_sha256": before["files"]["assembly/private/classifier.token"],
            "ca_sha256": before["files"]["assembly/tls/gr-platform/classifier/ca.crt"]}
        value = self._exec(runtime, READY_SCRIPT, settings, deadline=deadline)
        require(set(value) == {"status", "tls", "epoch", "attempts", "identity_sha256", "elapsed_ms"}
                and type(value["status"]) is int and value["status"] == 200 and value["tls"] in {"TLSv1.2", "TLSv1.3"}
                and value["identity_sha256"] == hashlib.sha256(canonical(identity())).hexdigest()
                and type(value["epoch"]) is str and type(value["attempts"]) is int
                and type(value["elapsed_ms"]) in (int, float) and 0 <= value["elapsed_ms"] < 8000)
        counter = snapshot([(b"x-evolution-classifier-worker-epoch", value["epoch"].encode("ascii")),
            (b"x-evolution-classifier-dispatch-attempts", str(value["attempts"]).encode("ascii"))])
        return {"binding_sha256": bound["readiness_binding_sha256"], "counter": counter, "tls": value["tls"], "ready_elapsed_ms": value["elapsed_ms"]}

    def sink(self, *, positive, deadline):
        bound = self.binding_reader.bound
        settings = {"source_ip": bound["peers"]["allowed-team-a"]["pod_ip"], "destination_ip": bound["addresses"]["sink_service"],
            "ca_sha256": self.binding_reader.closure["sink_ca_sha256"], "positive": positive}
        value = self._exec("allowed-team-a", SINK_SCRIPT, settings, deadline=deadline)
        require(set(value) == {"counts", "tls"} and value["tls"] in {"TLSv1.2", "TLSv1.3"}
                and type(value["counts"]) is dict and set(value["counts"]) == {"8443", "9443", "8445"}
                and all(type(count) is int and 0 <= count <= 2**63-1 for count in value["counts"].values()))
        return value

    def dns(self, transport, position, *, deadline):
        require(transport in {"tcp", "udp"} and position in {"before", "after"})
        bound = self.binding_reader.bound
        settings = {"source_ip": bound["peers"]["allowed-team-a"]["pod_ip"], "server_ip": bound["addresses"]["dns_service"],
            "resolved_ip": bound["addresses"]["api_service"], "transport": transport}
        require(self._exec("allowed-team-a", DNS_SCRIPT, settings, deadline=deadline) == settings)
        return {"position": position, **settings}
