"""Bounded, opaque loopback relay for CRI port-forward's PostgreSQL close bug.

No TLS or PostgreSQL protocol is interpreted here. Production endpoints are fixed;
Service traffic still reaches PostgreSQL directly. One of sixteen accepted socket
slots is reserved for a bounded graceful overload rejection.
"""

import sys


def listening(port=15432, table="/proc/net/tcp"):
    """Check the kernel's listener table without opening a database connection."""
    try:
        with open(table, encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if (
                    len(fields) >= 4
                    and fields[1] == f"0100007F:{port:04X}"
                    and fields[3] == "0A"
                ):
                    return True
    except OSError, UnicodeError:
        pass
    return False


# A probe gets the same50m cgroup budget as the relay. Avoid importing the I/O
# machinery just to read one kernel table; this path creates no TCP connection.
if __name__ == "__main__" and sys.argv[1:] == ["--check"]:
    raise SystemExit(0 if listening() else 1)

import errno
import selectors
import signal
import socket
import time

LISTEN = ("127.0.0.1", 15432)
UPSTREAM = ("127.0.0.1", 5432)
MAX_CONNECTIONS = 16
BUFFER_LIMIT = 65536
CHUNK = 16384


def tune(sock):
    sock.setblocking(False)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class Session:
    def __init__(self, client, now, lifetime):
        self.sockets = [client, None]
        self.output = [bytearray(), bytearray()]
        self.read_open = [True, True]
        self.write_eof = [False, False]
        self.write_closed = [False, False]
        self.connecting = False
        self.connect_deadline = None
        self.deadline = now + lifetime
        self.draining = False
        self.upstream_write_failed = False
        self.closed = False


class Relay:
    """Single-threaded selector loop with fixed per-session memory and deadlines."""

    def __init__(
        self,
        listen=LISTEN,
        upstream=UPSTREAM,
        *,
        max_connections=16,
        connect_timeout=5.0,
        lifetime=1800.0,
        drain_timeout=2.0,
    ):
        if not 2 <= max_connections <= MAX_CONNECTIONS:
            raise ValueError("invalid_connection_limit")
        if any(value <= 0 for value in (connect_timeout, lifetime, drain_timeout)):
            raise ValueError("invalid_deadline")
        if listen[0] != "127.0.0.1" or upstream[0] != "127.0.0.1":
            raise ValueError("loopback_required")
        self.upstream = upstream
        self.max_connections = max_connections
        self.connect_timeout = connect_timeout
        self.lifetime = lifetime
        self.drain_timeout = drain_timeout
        self.sessions = set()
        self.stopping = False
        self.shutdown_started = False
        self.selector = selectors.DefaultSelector()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(listen)
            self.listener.listen(max_connections)
            self.listener.setblocking(False)
            self.address = self.listener.getsockname()
            self.wake_read, self.wake_write = socket.socketpair()
            self.wake_read.setblocking(False)
            self.wake_write.setblocking(False)
            self.selector.register(self.wake_read, selectors.EVENT_READ, "wake")
        except BaseException:
            self.listener.close()
            self.selector.close()
            raise

    def stop(self):
        self.stopping = True
        try:
            self.wake_write.send(b"x")
        except OSError:
            pass

    def _unregister(self, sock):
        try:
            self.selector.unregister(sock)
        except KeyError:
            pass

    def _drop_socket(self, session, index):
        sock = session.sockets[index]
        if sock is not None:
            self._unregister(sock)
            sock.close()
            session.sockets[index] = None

    def _close(self, session):
        if not session.closed:
            session.closed = True
            for index in (0, 1):
                self._drop_socket(session, index)
                session.output[index].clear()
            self.sessions.discard(session)

    def _drain(self, session, now):
        """Send queued upstream bytes then FIN; consume the client's final tail."""
        if session.draining:
            return
        self._drop_socket(session, 1)
        session.output[1].clear()
        session.connecting = False
        session.draining = True
        session.write_eof[0] = True
        session.deadline = (
            min(session.deadline, now + self.drain_timeout)
            if session.upstream_write_failed
            else now + self.drain_timeout
        )

    def _accept(self, now):
        # Accept at most one per selector turn; established streams retain service.
        if len(self.sessions) >= self.max_connections or self.stopping:
            return
        try:
            client, _ = self.listener.accept()
        except BlockingIOError, InterruptedError:
            return
        session = Session(client, now, self.lifetime)
        self.sessions.add(session)
        try:
            tune(client)
            if len(self.sessions) >= self.max_connections:
                self._drain(session, now)
                return
            backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            session.sockets[1] = backend
            tune(backend)
            result = backend.connect_ex(self.upstream)
            if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                self._drain(session, now)
                return
            session.connecting = result != 0
            session.connect_deadline = now + self.connect_timeout
        except OSError:
            self._drain(session, now)

    def _read(self, session, index, now):
        other = 1 - index
        discard = (session.draining or session.upstream_write_failed) and index == 0
        capacity = (
            CHUNK if discard else min(CHUNK, BUFFER_LIMIT - len(session.output[other]))
        )
        if capacity <= 0:
            return
        try:
            data = session.sockets[index].recv(capacity)
        except BlockingIOError, InterruptedError:
            return
        except OSError:
            if index == 1:
                self._drain(session, now)
            else:
                self._close(session)
            return
        if not data:
            session.read_open[index] = False
            session.write_eof[other] = True
            if index == 1 and session.upstream_write_failed:
                self._drain(session, now)
        elif not discard:
            session.output[other].extend(data)

    def _write(self, session, index, now):
        try:
            count = session.sockets[index].send(session.output[index])
            if count:
                del session.output[index][:count]
        except BlockingIOError, InterruptedError:
            return
        except OSError:
            if index == 1:
                # A failed write does not consume readable final ciphertext.
                # Keep reading this socket with normal backpressure until EOF/RST,
                # sharing the same two-second terminal drain deadline.
                session.upstream_write_failed = True
                session.output[1].clear()
                session.deadline = min(session.deadline, now + self.drain_timeout)
            else:
                self._close(session)

    def _sync(self, session):
        if session.closed:
            return
        if session.draining and not session.read_open[0] and not session.output[0]:
            self._close(session)
            return
        if not any(session.read_open) and not any(session.output):
            self._close(session)
            return
        for index, sock in enumerate(session.sockets):
            if sock is None:
                continue
            if (
                session.write_eof[index]
                and not session.output[index]
                and not session.write_closed[index]
                and not (index == 1 and session.connecting)
            ):
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                session.write_closed[index] = True
            events = 0
            if (
                session.read_open[index]
                and (session.draining or len(session.output[1 - index]) < BUFFER_LIMIT)
                and not (index == 1 and session.connecting)
            ):
                events |= selectors.EVENT_READ
            if session.output[index] or (index == 1 and session.connecting):
                events |= selectors.EVENT_WRITE
            if events:
                try:
                    self.selector.modify(sock, events, (session, index))
                except KeyError:
                    self.selector.register(sock, events, (session, index))
            else:
                self._unregister(sock)

    def _listener_events(self):
        if not self.stopping and len(self.sessions) < self.max_connections:
            try:
                self.selector.get_key(self.listener)
            except KeyError:
                self.selector.register(self.listener, selectors.EVENT_READ, "accept")
        else:
            self._unregister(self.listener)

    def run(self):
        try:
            while not self.stopping or self.sessions:
                now = time.monotonic()
                if self.stopping and not self.shutdown_started:
                    self.shutdown_started = True
                    for session in tuple(self.sessions):
                        self._drain(session, now)
                deadlines = [now + 1.0]
                for session in tuple(self.sessions):
                    if now >= session.deadline:
                        if session.draining or session.upstream_write_failed:
                            self._close(session)
                        else:
                            self._drain(session, now)
                    elif session.connecting and now >= session.connect_deadline:
                        self._drain(session, now)
                    if not session.closed:
                        deadlines.append(session.deadline)
                        if session.connecting:
                            deadlines.append(session.connect_deadline)
                        self._sync(session)
                self._listener_events()
                if self.stopping and not self.sessions:
                    break
                for key, mask in self.selector.select(
                    max(0, min(deadlines) - time.monotonic())
                ):
                    if key.data == "wake":
                        try:
                            self.wake_read.recv(4096)
                        except BlockingIOError:
                            pass
                    elif key.data == "accept":
                        self._accept(time.monotonic())
                    else:
                        session, index = key.data
                        if session.closed or session.sockets[index] is None:
                            continue
                        now = time.monotonic()
                        if index == 1 and session.connecting:
                            if key.fileobj.getsockopt(
                                socket.SOL_SOCKET, socket.SO_ERROR
                            ):
                                self._drain(session, now)
                                continue
                            session.connecting = False
                        if mask & selectors.EVENT_READ:
                            self._read(session, index, now)
                        if (
                            mask & selectors.EVENT_WRITE
                            and not session.closed
                            and session.sockets[index] is not None
                            and session.output[index]
                        ):
                            self._write(session, index, now)
        finally:
            for session in tuple(self.sessions):
                self._close(session)
            self.listener.close()
            self.wake_read.close()
            self.wake_write.close()
            self.selector.close()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--check"]:
        return 0 if listening() else 1
    if args:
        return 2
    try:
        relay = Relay()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: relay.stop())
        relay.run()
    except OSError, ValueError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
