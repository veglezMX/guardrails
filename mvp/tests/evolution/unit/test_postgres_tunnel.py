"""Real loopback TCP/TLS regression tests for the transparent CRI relay."""

import contextlib
import fcntl
import importlib.util
import queue
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / f"deploy/evolution/{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tunnel = load("postgres_tunnel")


def eventually(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


class Backend:
    def __init__(self, handler):
        self.handler = handler
        self.errors = queue.Queue()
        self.done = threading.Event()
        self.connections = []
        self.threads = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.listener.settimeout(0.1)
        self.address = self.listener.getsockname()
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.done.is_set():
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections.append(conn)
            thread = threading.Thread(target=self.handle, args=(conn,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def handle(self, conn):
        try:
            conn.settimeout(3)
            self.handler(conn)
        except BaseException as error:  # noqa: BLE001 - relay failures propagate to test thread
            if not self.done.is_set():
                self.errors.put(error)
        finally:
            conn.close()

    def close(self):
        self.done.set()
        self.listener.close()
        for conn in self.connections:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        self.thread.join(2)
        for thread in self.threads:
            thread.join(2)
        assert not self.thread.is_alive()
        assert not any(thread.is_alive() for thread in self.threads)
        if not self.errors.empty():
            raise self.errors.get()


@contextlib.contextmanager
def running(upstream, **kwargs):
    relay = tunnel.Relay(("127.0.0.1", 0), upstream, **kwargs)
    errors = queue.Queue()

    def target():
        try:
            relay.run()
        except BaseException as error:  # noqa: BLE001 - relay failures propagate to test thread
            errors.put(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        yield relay
    finally:
        relay.stop()
        thread.join(4)
        assert not thread.is_alive()
        assert not relay.sessions
        if not errors.empty():
            raise errors.get()


def connect(relay):
    return socket.create_connection(relay.address, timeout=3)


def receive_all(conn):
    result = bytearray()
    while chunk := conn.recv(8192):
        result.extend(chunk)
    return bytes(result)


def echo(conn):
    while chunk := conn.recv(8192):
        conn.sendall(chunk)


def test_upstream_reset_becomes_fin_and_does_not_interrupt_other_stream(capsys):
    reset = threading.Event()

    def handler(conn):
        tag = conn.recv(1)
        if tag == b"R":
            conn.sendall(b"ciphertext-already-queued")
            assert reset.wait(3)
            conn.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
        else:
            echo(conn)

    backend = Backend(handler)
    try:
        with (
            running(backend.address) as relay,
            connect(relay) as stable,
            connect(relay) as failed,
        ):
            stable.sendall(b"E")
            failed.sendall(b"R")
            assert failed.recv(128) == b"ciphertext-already-queued"
            reset.set()
            assert failed.recv(1) == b""  # A propagated RST raises instead.
            failed.sendall(b"encrypted-client-close-tail")
            failed.shutdown(socket.SHUT_WR)
            eventually(lambda: len(relay.sessions) == 1)
            stable.sendall(b"peer-still-usable")
            assert stable.recv(128) == b"peer-still-usable"
            with connect(relay) as later:
                later.sendall(b"Eafter-reset")
                assert later.recv(128) == b"after-reset"
    finally:
        reset.set()
        backend.close()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_client_half_close_preserves_complete_large_response_and_backpressure():
    request = bytes(range(256)) * 1024
    response = bytes(reversed(range(256))) * 1024
    received = queue.Queue()

    def handler(conn):
        received.put(receive_all(conn))
        conn.sendall(response)
        conn.shutdown(socket.SHUT_WR)

    backend = Backend(handler)
    try:
        with running(backend.address) as relay, connect(relay) as client:
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            assert receive_all(client) == response
            assert received.get(timeout=2) == request
            assert all(
                len(buf) <= tunnel.BUFFER_LIMIT
                for s in relay.sessions
                for buf in s.output
            )
            eventually(lambda: not relay.sessions)
    finally:
        backend.close()


def test_upstream_half_close_keeps_client_tail_direction_open():
    tail = queue.Queue()

    def handler(conn):
        conn.sendall(b"response")
        conn.shutdown(socket.SHUT_WR)
        tail.put(receive_all(conn))

    backend = Backend(handler)
    try:
        with running(backend.address) as relay, connect(relay) as client:
            assert receive_all(client) == b"response"
            client.sendall(b"tail-after-server-fin")
            client.shutdown(socket.SHUT_WR)
            assert tail.get(timeout=2) == b"tail-after-server-fin"
            eventually(lambda: not relay.sessions)
    finally:
        backend.close()


def test_verified_tls_passes_through_opaque_relay_without_termination(tmp_path, capsys):
    certificates = load("certificates")
    certificates.prepare(tmp_path / "credentials")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        tmp_path / "credentials/server.crt", tmp_path / "credentials/server.key"
    )
    client_context = ssl.create_default_context(cafile=tmp_path / "credentials/ca.crt")
    observed = queue.Queue()

    def handler(conn):
        with server_context.wrap_socket(conn, server_side=True) as tls:
            observed.put(tls.version())
            data = tls.recv(4096)
            tls.sendall(data)
            plain = tls.unwrap()
            plain.close()

    backend = Backend(handler)
    try:
        with running(backend.address) as relay:
            for _ in range(3):
                with client_context.wrap_socket(
                    connect(relay), server_hostname="localhost"
                ) as client:
                    client.sendall(b"synthetic-sensitive-opaque-canary")
                    assert client.recv(4096) == b"synthetic-sensitive-opaque-canary"
                    assert client.version() == observed.get(timeout=2)
                    assert client.getpeercert()["subject"]
                    plain = client.unwrap()
                    plain.close()
            eventually(lambda: not relay.sessions)
    finally:
        backend.close()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_connection_cap_reserves_bounded_fin_rejection_and_keeps_admitted_streams():
    backend = Backend(echo)
    try:
        with running(backend.address, max_connections=3, drain_timeout=0.2) as relay:  # noqa: SIM117
            with connect(relay) as first, connect(relay) as second:
                for client in (first, second):
                    client.sendall(b"accepted")
                    assert client.recv(32) == b"accepted"
                with connect(relay) as rejected:
                    assert rejected.recv(1) == b""
                    assert len(relay.sessions) == 3
                    assert len(backend.connections) == 2
                    rejected.sendall(b"discarded-tail")
                    eventually(lambda: len(relay.sessions) == 2)
                first.sendall(b"still-admitted")
                assert first.recv(32) == b"still-admitted"
    finally:
        backend.close()


def test_pending_connect_deadline_and_failed_connect_return_fin():
    # Linux's loopback SYN queue can be filled without consuming a remote service.
    with socket.socket() as saturated:
        saturated.bind(("127.0.0.1", 0))
        saturated.listen(1)
        address = saturated.getsockname()
        with (  # noqa: SIM117
            socket.create_connection(address, timeout=1),
            socket.create_connection(address, timeout=1),
        ):
            with running(address, connect_timeout=0.15, drain_timeout=0.1) as relay:
                start = time.monotonic()
                with connect(relay) as client:
                    client.sendall(b"queued-until-connect-timeout")
                    assert client.recv(1) == b""
                    assert 0.1 <= time.monotonic() - start < 1
                    client.shutdown(socket.SHUT_WR)
                    eventually(lambda: not relay.sessions)
        port = address[1]
    with (
        running(("127.0.0.1", port), drain_timeout=0.1) as relay,
        connect(relay) as client,
    ):
        assert client.recv(1) == b""
        client.shutdown(socket.SHUT_WR)
        eventually(lambda: not relay.sessions)


def test_lifetime_and_shutdown_bound_uncooperative_connection_cleanup():
    backend = Backend(echo)
    try:
        with running(backend.address, lifetime=0.15, drain_timeout=0.15) as relay:  # noqa: SIM117
            with connect(relay) as client:
                start = time.monotonic()
                client.sendall(b"live")
                assert client.recv(16) == b"live"
                assert client.recv(1) == b""
                client.sendall(b"tail-without-client-fin")
                eventually(lambda: not relay.sessions)
                assert time.monotonic() - start < 1
        with (
            running(backend.address, drain_timeout=0.15) as relay,
            connect(relay) as client,
        ):
            client.sendall(b"active")
            assert client.recv(16) == b"active"
            start = time.monotonic()
            relay.stop()
            assert client.recv(1) == b""
            eventually(lambda: not relay.sessions)
            assert time.monotonic() - start < 1
    finally:
        backend.close()


def test_passive_health_cli_and_signals_never_connect_to_database(tmp_path):
    script = ROOT / "deploy/evolution/postgres_tunnel.py"
    with socket.socket() as listener:
        listener.bind(tunnel.LISTEN)
        listener.listen(1)
        listener.settimeout(0.05)
        result = subprocess.run(
            [sys.executable, "-S", "-B", str(script), "--check"],
            check=False,
            capture_output=True,
            timeout=3,
        )
        assert result.returncode == 0
        assert result.stdout == result.stderr == b""
        with pytest.raises(TimeoutError):
            listener.accept()
        # The constrained probe must not import the relay's full socket machinery.
        audit = """import sys
sys.argv = [sys.argv[1], '--check']
def guard(event, args):
    if event == 'import' and not args[0].startswith('encodings.'):
        raise RuntimeError('probe_imported_runtime_dependency')
sys.addaudithook(guard)
exec(compile(open(sys.argv[0]).read(), sys.argv[0], 'exec'), {'__name__': '__main__'})
"""
        lean = subprocess.run(
            [sys.executable, "-S", "-B", "-c", audit, str(script)],
            check=False,
            capture_output=True,
            timeout=3,
        )
        assert lean.returncode == 0
        assert lean.stdout == lean.stderr == b""
        with pytest.raises(TimeoutError):
            listener.accept()
    result = subprocess.run(
        [sys.executable, "-S", "-B", str(script), "--check"],
        check=False,
        capture_output=True,
        timeout=3,
    )
    assert result.returncode == 1
    assert result.stdout == result.stderr == b""
    child = subprocess.Popen(
        [sys.executable, "-B", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        eventually(tunnel.listening)
        child.terminate()
        stdout, stderr = child.communicate(timeout=3)
        assert child.returncode == 0
        assert stdout == stderr == b""
        assert not tunnel.listening()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
    table = tmp_path / "tcp"
    table.write_text(
        "header\n0: 00000000:3C48 00000000:0000 0A\n0: 0100007F:3C48 00000000:0000 01\n"
    )
    assert not tunnel.listening(table=table)
    assert not tunnel.listening(table=tmp_path / "missing")


def test_reset_during_backpressure_preserves_already_queued_upstream_bytes():
    queued = threading.Event()
    reset = threading.Event()
    payload = bytes(range(256)) * 512

    def handler(conn):
        conn.sendall(payload)
        eventually(
            lambda: (
                struct.unpack("i", fcntl.ioctl(conn, 0x5411, struct.pack("i", 0)))[0]
                == 0
            )
        )
        queued.set()
        assert reset.wait(3)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

    backend = Backend(handler)
    try:
        with running(backend.address) as relay, socket.socket() as client:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            client.settimeout(3)
            client.connect(relay.address)
            assert queued.wait(2)
            eventually(
                lambda: any(
                    len(s.output[0]) == tunnel.BUFFER_LIMIT for s in relay.sessions
                )
            )
            reset.set()
            # The backend's write direction can fail while readable ciphertext
            # remains queued; the relay must not close that readable socket early.
            client.sendall(b"opaque-final-client-tail" * 1024)
            assert receive_all(client) == payload
            client.shutdown(socket.SHUT_WR)
            eventually(lambda: not relay.sessions)
    finally:
        reset.set()
        backend.close()
