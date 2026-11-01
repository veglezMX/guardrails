"""Bound direct-IP denial primitive; imports perform no network or host work.

This is not a completed CNI acceptance case. Parent must compose a source-bound
original binding capture, sink/DNS positive controls, ready bracketing, immutable
launch provenance and failed/partial evidence retention before registration.
"""
from __future__ import annotations

import hashlib

from evolution import classifier_cni_evidence as evidence
from guardrails.evolution.classifier_contract import canonical, parse

from mvp.tests.evolution.e2e.classifier_network_support import private_bytes, require

DENY_SCRIPT = r'''
import ipaddress,json,signal,socket,sys,time

def need(value):
    if not value: raise ValueError('classifier_cni_denial_invalid')

def perform(settings):
    need(type(settings) is dict and set(settings) == {'source_ip','destination_ip','port','transport'})
    source = ipaddress.IPv4Address(settings['source_ip'])
    target = ipaddress.IPv4Address(settings['destination_ip'])
    need(str(source) == settings['source_ip'] and source.is_private
         and not (source.is_loopback or source.is_multicast or source.is_unspecified or source.is_link_local)
         and str(target) == settings['destination_ip'] and not (target.is_loopback or target.is_multicast or target.is_unspecified)
         and type(settings['port']) is int and 1 <= settings['port'] <= 65535
         and settings['transport'] in ('tcp','udp') and (settings['transport'] != 'udp' or settings['port'] == 53))
    stream = settings['transport'] == 'tcp'
    channel = socket.socket(socket.AF_INET, socket.SOCK_STREAM if stream else socket.SOCK_DGRAM)
    try:
        channel.bind((str(source),0))
        channel.settimeout(2.0)
        started = time.monotonic()
        try:
            if stream:
                channel.connect((str(target),settings['port']))
            else:
                # Fixed synthetic A query, direct IP; libc DNS is never used.
                query = b'\x47\x52\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
                for part in ('kubernetes','default','svc','cluster','local'):
                    query += bytes([len(part)]) + part.encode('ascii')
                query += b'\x00\x00\x01\x00\x01'
                need(channel.sendto(query,(str(target),53)) == len(query))
                channel.recvfrom(512)
        except TimeoutError:
            elapsed = (time.monotonic()-started)*1000
            need(1800 <= elapsed <= 3500 and channel.getsockname()[0] == str(source))
            return {**settings,'outcome':'timeout','elapsed_ms':round(elapsed,3)}
        # Accepted TCP, any DNS reply, refusal, ICMP/network errors or early DNS
        # errors are failures. No payload or credential is sent over TCP.
        raise ValueError('classifier_cni_connection_not_denied')
    finally:
        channel.close()

def pairs(items):
    value = {}
    for key,item in items:
        need(key not in value)
        value[key] = item
    return value

if __name__ == '__main__':
    signal.alarm(5)
    try:
        need(len(sys.argv) == 2 and len(sys.argv[1]) <= 4096)
        settings = json.loads(sys.argv[1],object_pairs_hook=pairs,parse_constant=lambda _: need(False))
        print(json.dumps(perform(settings),sort_keys=True,separators=(',',':'),allow_nan=False))
    except Exception:
        print('{"status":"failed"}')
        raise SystemExit(1) from None
'''


class CniSocketReader:
    def __init__(self, observer, capture_binding):
        self.observer = observer
        # Future concrete capture must observe original native identities. This
        # dependency is deliberately explicit; there is no permissive default.
        require(callable(capture_binding))
        self.capture_binding = capture_binding

    def probe_locked(self, bound, job_id, *, deadline):
        evidence.bindings(bound)
        observer, d = self.observer, self.observer.deployment
        require(observer.bound is not None and bound["owner_id"] == observer.bound["owner_id"]
                and bound["node_id"] == observer.bound["node_id"]
                and bound["readiness_binding_sha256"] == hashlib.sha256(canonical(observer.bound)).hexdigest())
        observer.deadline = deadline
        observer._remaining()
        require(self.capture_binding(deadline=deadline) == bound)
        expected = [job for job in evidence.jobs(bound) if job["job_id"] == job_id]
        require(len(expected) == 1)
        job = expected[0]
        origin = bound["peers"][job["origin"]]
        require(d.kubeconfig == d.state / "kubeconfig")
        for name, digest in observer._api_pins.items():
            raw = private_bytes(d.state / name, deadline=deadline,
                modes=(0o600,) if name in observer._api_private else (0o600, 0o644))
            require(hashlib.sha256(raw).hexdigest() == digest)
        require(observer._native(observer._api_owner, observer._api_images) == observer._api_server)
        python = "/opt/classifier/venv/bin/python" if job["origin"] == "classifier" else "python"
        raw = observer._command([d.bin / "kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution",
            "--namespace", origin["namespace"], "--request-timeout=6s", "exec", origin["pod_name"], "-c", origin["container"],
            "--", python, "-B", "-c", DENY_SCRIPT, canonical(job["settings"]).decode()], maximum=4096, timeout=7, input_text=observer._api_input)
        value = evidence.result(parse(raw.encode(), limit=4096), job)
        require(self.capture_binding(deadline=deadline) == bound)
        observer._remaining()
        return {"job_id": job_id, "result": value}
