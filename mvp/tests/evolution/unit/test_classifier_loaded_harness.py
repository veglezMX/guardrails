"""Actual production pool/ASGI composition with fake process pipes, never ML."""
import asyncio
import copy
import errno
import json
import os
import signal
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_loaded_evidence as e
from guardrails.evolution import classifier_contract as contract

from services.classifier import loaded_acceptance as h


@pytest.fixture(autouse=True)
def forbid_real_dispatch(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real process or network dispatch forbidden")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def test_output_rows_are_immediately_private_durable_and_exclusive(tmp_path):
    output = tmp_path / "output"
    writer = h.Writer(output)
    try:
        writer.write("started.json", b"{}\n")
        writer.append_row({"case": "original"})
        assert (output / "cases.jsonl").read_bytes() == b'{"case":"original"}\n'
        assert (output / "cases.jsonl").stat().st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError): writer.write("started.json", b"replacement")
        assert (output / "started.json").read_bytes() == b"{}\n"
    finally:
        writer.close()


@pytest.mark.parametrize("fault", ["root", "ancestor", "row", "row-mode", "row-hardlink"])
def test_descriptor_writer_refuses_redirected_or_changed_evidence(tmp_path, fault):
    parent = tmp_path / "parent"; parent.mkdir(mode=0o700)
    output = parent / "output"
    writer = h.Writer(output)
    writer.append_row({"case": "original"})
    try:
        if fault == "root": output.rename(parent / "held"); output.mkdir(mode=0o700)
        elif fault == "ancestor": parent.rename(tmp_path / "held"); parent.mkdir(mode=0o700); output.mkdir(mode=0o700)
        elif fault == "row": (output / "cases.jsonl").rename(output / "held"); (output / "cases.jsonl").write_bytes(b"foreign")
        elif fault == "row-mode": (output / "cases.jsonl").chmod(0o644)
        else: os.link(output / "cases.jsonl", output / "held")
        with pytest.raises(e.LoadedEvidenceError): writer.append_row({"case": "new"})
        if fault in {"root", "ancestor"}: assert list(output.iterdir()) == []
        elif fault == "row": assert (output / "cases.jsonl").read_bytes() == b"foreign"
    finally:
        writer.close()


def isolated_network(*, tunnel=False, ipv6_errno=101):
    links = [{"name": "lo", "index": 1, "hardware_type": 772, "flags": 8,
              "kind": None, "operstate": 2, "carrier": 1, "link_index": 0,
              "master_index": 0, "foreign_namespace_link": False, "tunnel": None,
              "address_count": 0, "route_count": 0}]
    if tunnel:
        links.append({"name": "tunl0", "index": 2, "hardware_type": 768, "flags": 128,
            "kind": "ipip", "operstate": 2, "carrier": 1, "link_index": 0,
            "master_index": 0, "foreign_namespace_link": False,
            "address_count": 0, "route_count": 0,
            "tunnel": {"link_index": 0, "local_zero": True, "remote_zero": True,
                "ttl": 0, "tos": 0, "protocol": 0, "pmtudisc": 0,
                "encapsulation_type": 0, "encapsulation_flags": 0,
                "source_port_zero": True, "destination_port_zero": True,
                "fwmark": 0, "collect_metadata": False}})
    return {"schema_version": e.network_isolation.SCHEMA,
            "namespace": "net:[2]", "host_namespace": "net:[1]",
            "interfaces": [row["name"] for row in links], "links": links,
            "external_routes": 0, "ipv4_connect_errno": 101,
            "ipv6_connect_errno": ipv6_errno}


class SocketProbe:
    def __init__(self, error): self.error = error
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, value): assert value == .25
    def connect(self, address):
        assert address in {("198.51.100.1", 443), ("2001:db8::1", 443, 0, 0)}
        if self.error is not None: raise OSError(self.error, "arbitrary text must never be retained")


@pytest.mark.parametrize("fault", [None, "same-namespace", "interface", "route", "timeout", "connected"])
def test_network_guard_precedes_any_model_start(monkeypatch, fault):
    value = isolated_network()
    value.pop("ipv4_connect_errno"); value.pop("ipv6_connect_errno")
    if fault == "same-namespace": value["namespace"] = "net:[1]"
    elif fault == "interface": value["interfaces"].append("eth0")
    elif fault == "route": value["external_routes"] = 1
    monkeypatch.setattr(os, "readlink", lambda path: value["namespace"])
    monkeypatch.setattr(h.network_isolation, "observe", lambda *args, **kwargs: copy.deepcopy(value))
    calls = []
    def create(*args):
        calls.append(args)
        return SocketProbe(None if fault == "connected" else errno.ETIMEDOUT if fault == "timeout" else errno.ENETUNREACH)
    monkeypatch.setattr(socket, "socket", create)
    if fault is None:
        result = h.network_snapshot("net:[1]")
        assert result["ipv4_connect_errno"] == result["ipv6_connect_errno"] == errno.ENETUNREACH
        assert len(calls) == 2
    else:
        with pytest.raises(e.LoadedEvidenceError): h.network_snapshot("net:[1]")
        if fault in {"same-namespace", "interface", "route"}: assert calls == []


@pytest.mark.parametrize("tunnel,ipv6", [(False, 101), (True, 101), (False, 99), (True, 99)])
def test_actual_shared_snapshot_is_reused_without_normalizing_native_errno(monkeypatch, tunnel, ipv6):
    value = isolated_network(tunnel=tunnel, ipv6_errno=ipv6)
    observed = {k: copy.deepcopy(v) for k, v in value.items() if not k.endswith("_errno")}
    calls = []
    monkeypatch.setattr(os, "readlink", lambda path: "net:[2]")
    def observe(host, *, deadline):
        assert host == "net:[1]" and deadline > 0
        calls.append("observe")
        return copy.deepcopy(observed)
    monkeypatch.setattr(h.network_isolation, "observe", observe)
    monkeypatch.setattr(socket, "socket", lambda family, kind: SocketProbe(101 if family == socket.AF_INET else ipv6))
    assert h.network_snapshot("net:[1]") == value
    assert calls == ["observe", "observe"]


@pytest.mark.parametrize("when", ["before", "after"])
def test_calling_thread_namespace_drift_refuses_and_never_uses_foreign_namespace(monkeypatch, when):
    calls = []
    def readlink(path):
        return "net:[3]" if path == "/proc/thread-self/ns/net" and (when == "before" or calls) else "net:[2]"
    monkeypatch.setattr(os, "readlink", readlink)
    def snapshot(host):
        calls.append(host)
        return isolated_network()
    monkeypatch.setattr(h.network_isolation, "snapshot", snapshot)
    with pytest.raises(e.LoadedEvidenceError, match="network_thread_namespace"):
        h.network_snapshot("net:[1]")
    assert calls == ([] if when == "before" else ["net:[1]"])


def fake_engine(monkeypatch, *, tail_fault=None):
    from services.classifier import worker
    def denied(*args, **kwargs): raise AssertionError("model loading is forbidden")
    monkeypatch.setattr(worker, "load_engine", denied)

    class Tokenizer:
        def num_special_tokens_to_add(self, pair=False): return 2
        def encode(self, text, **kwargs): return [99 if word == "Ignore" else 1 for word in text.split()]
        def build_inputs_with_special_tokens(self, tokens): return [2, *tokens, 3]

    monkeypatch.setattr(h, "load_tokenizer", lambda path: Tokenizer())

    processes = []

    class Process:
        def __init__(self):
            self.pid = 900001 + len(processes)
            self.returncode, self.paused = None, False
            self.queue, self.death = asyncio.Queue(), asyncio.Event()
            self.stdout = self.stdin = self
            self.writes, self.reaps, self.kills = [], 0, 0
            ready = contract.canonical({"api_major": 1, "ready": True, "identity": contract.identity()}) + b"\n"
            asyncio.get_running_loop().call_later(.03, self.queue.put_nowait, ready)
        def write(self, raw):
            envelope = contract.parse(raw[:-1])
            self.writes.append(envelope)
            if not self.paused:
                try:
                    predict = lambda tokens: .99 if 99 in tokens and tail_fault != "miss" else .4
                    value = worker.classify(envelope["request"], Tokenizer(), predict, envelope["deadline"])
                    if tail_fault == "prefix-only" and h.LONG_TAIL_ATTACK in envelope["request"]["segments"][0]["text"]:
                        value["windows"] = value["scores"][0]["windows"] = 1
                    if tail_fault == "prefix-hit" and envelope["request"]["segments"][0]["text"] == h.LONG_TAIL_PREFIX:
                        value["scores"][0]["score"] = .99
                except contract.ClassifierError as error:
                    value = {"error": error.code}
                self.queue.put_nowait(contract.canonical(value) + b"\n")
        async def drain(self): pass
        async def readline(self): return await self.queue.get()
        def send_signal(self, value):
            assert value == signal.SIGSTOP
            self.paused = True
        def kill(self):
            self.kills += 1; self.returncode = -signal.SIGKILL
            self.queue.put_nowait(b""); self.death.set()
        async def wait(self):
            await self.death.wait()
            return self.returncode
        async def communicate(self):
            self.reaps += 1
            await self.death.wait()
            return b"", b""

    async def spawn(*args, **kwargs):
        assert args[1:] == ("-B", "-s", "-m", "services.classifier.worker", "/synthetic-model")
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1" and kwargs["stderr"] is asyncio.subprocess.DEVNULL
        process = Process(); processes.append(process)
        return process

    original_read, original_link = Path.read_text, os.readlink
    def read(path, *args, **kwargs):
        for process in processes:
            if str(path) == f"/proc/{process.pid}/status":
                return "State:\t" + ("T (stopped)" if process.paused else "S (sleeping)") + "\n"
        return original_read(path, *args, **kwargs)
    def link(path, *args, **kwargs):
        if str(path) in {f"/proc/{p.pid}/ns/net" for p in processes}: return "net:[2]"
        return original_link(path, *args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(os, "readlink", link)
    return processes


def test_actual_workerpool_asgi_and_harness_compose_without_model_or_process(monkeypatch):
    processes = fake_engine(monkeypatch)
    observed = []
    result = asyncio.run(h.run("/synthetic-model", "net:[2]", lambda *row: observed.append(row)))
    assert result["request_count"] == 46 and result["closed"] is True
    assert [row[0] for row in observed] == list(e.CASES[1:])
    assert len(processes) == 6
    assert [len(p.writes) for p in processes] == [3, 2, 1, 4, 2, 1]
    assert all(p.returncode == -signal.SIGKILL and p.reaps >= 1 for p in processes)
    assert all(0 <= row[2] <= 3000 for row in observed)
    assert all(row[1] >= row[2] for row in observed)
    tail = next(row[3] for row in observed if row[0] == "long_tail")
    assert tail["shared_prefix_tokens"] >= 512
    assert tail["inspected_windows"] == tail["expected_windows"] > 1


@pytest.mark.parametrize("fault", ["miss", "prefix-only", "prefix-hit"])
def test_actual_workerpool_tail_refuses_missed_tail_partial_coverage_and_false_positive(monkeypatch, fault):
    from services.classifier.app import ClassifierApplication, WorkerPool
    processes = fake_engine(monkeypatch, tail_fault=fault)
    async def run():
        pool = WorkerPool("/synthetic-model")
        app = ClassifierApplication(pool, bearer_token=h.TOKEN)
        await pool.start()
        try:
            with pytest.raises(e.LoadedEvidenceError):
                await h.long_tail(pool, app, "/synthetic-model")
        finally:
            await pool.close()
    asyncio.run(run())
    assert len(processes) == 1 and len(processes[0].writes) == 2
    assert processes[0].returncode == -signal.SIGKILL


@pytest.mark.parametrize("fault", ["short", "changed-prefix", "over-limit", "special"])
def test_tail_position_guard_uses_actual_tokenizer_before_http_dispatch(fault):
    class Tokenizer:
        def num_special_tokens_to_add(self, pair=False): return 256 if fault == "special" else 2
        def encode(self, text, **kwargs):
            count = 100 if fault == "short" else 5000 if fault == "over-limit" else 800
            if text == h.LONG_TAIL_PREFIX: return [1] * count
            return ([2] * count if fault == "changed-prefix" else [1] * count) + [99] * 50
    with pytest.raises(e.LoadedEvidenceError): h.long_tail_layout(Tokenizer())


def test_pipe_observer_forwards_identity_results_failures_and_cleanup_without_raw_retention():
    expected = b'{"request":{"request_id":"synthetic"}}\n'
    sentinel = object()
    class Pipe:
        def __init__(self): self.writes = []; self.closed = False
        def write(self, raw):
            self.writes.append(raw)
            if raw == b"error": raise RuntimeError("original")
            return sentinel
        async def drain(self): return sentinel
        def close(self): self.closed = True
    original = Pipe(); observer = h.PipeObserver(original)
    assert observer.write(expected) is sentinel and original.writes[0] is expected
    assert observer.ids == ["synthetic"] and observer.valid
    assert asyncio.run(observer.drain()) is sentinel
    observer.close(); assert original.closed
    with pytest.raises(RuntimeError, match="original"): observer.write(b"error")
    assert observer.write(b"unknown") is sentinel and observer.valid is False
    assert observer.ids == ["synthetic"]


def test_owned_child_must_match_isolated_namespace_before_signal(monkeypatch):
    class Process:
        pid = 900001
        def send_signal(self, value): pytest.fail("signal before namespace validation")
    pool = SimpleNamespace(ready=lambda: True, process=Process())
    monkeypatch.setattr(os, "readlink", lambda path: "net:[99]")
    with pytest.raises(e.LoadedEvidenceError, match="worker_namespace"):
        asyncio.run(h.stop_worker(pool, "net:[2]"))


def test_main_keeps_original_prefix_and_finite_failure_without_exception_text(tmp_path, monkeypatch, capsys):
    sources = dict.fromkeys(e.SOURCE_FILES, "a" * 64)
    network = isolated_network()
    monkeypatch.setattr(h, "sys", SimpleNamespace(version_info=(3, 13, 16), dont_write_bytecode=True))
    monkeypatch.setattr(h, "source_snapshot", lambda root: sources)
    monkeypatch.setattr(h, "network_snapshot", lambda host: network)
    async def failed(model, namespace, emit):
        emit("loaded_http", 1, 1)
        raise ValueError("sensitive exception and prompt must not escape")
    monkeypatch.setattr(h, "run", failed)
    output = tmp_path / "attempt"
    args = ["--run-loaded-acceptance", "--model-directory", "/synthetic-model", "--output", str(output),
            "--source-digest", e.digest(e.canonical(sources)), "--host-network-namespace", "net:[1]"]
    previous = os.umask(0o077)
    try: assert h.main(args) == 1
    finally: os.umask(previous)
    assert not (output / "result.json").exists()
    assert json.loads((output / "failure.json").read_bytes()) == {"status": "failed", "completed_cases": 2,
                                                              "stage": "complete_coverage_refusal"}
    assert len((output / "cases.jsonl").read_bytes().splitlines()) == 2
    assert "sensitive" not in b"".join(p.read_bytes() for p in output.iterdir()).decode()
    assert capsys.readouterr().out == ""
