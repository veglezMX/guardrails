"""Loopback-only, fixed-destination TLS passthrough for internal Docker networks.

Some Docker engines omit NAT bindings on internal-only bridges. This host
listener forwards encrypted bytes to one inspected local container address;
no HTTP parsing, certificate termination, external destination or egress relay.
"""
import select
import socket
import socketserver
import threading


class LoopbackForwarder:
    def __init__(self, port, target):
        self.target = target
        self.slots = threading.BoundedSemaphore(128)
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not owner.slots.acquire(blocking=False):
                    return
                try:
                    with socket.create_connection(owner.target, timeout=1) as remote:
                        self.request.settimeout(15)
                        remote.settimeout(15)
                        while True:
                            readable, _, _ = select.select([self.request, remote], [], [], 15)
                            if not readable:
                                return
                            for source in readable:
                                data = source.recv(65536)
                                if not data:
                                    return
                                (remote if source is self.request else self.request).sendall(data)
                except OSError:
                    pass
                finally:
                    owner.slots.release()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        self.server = Server(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
