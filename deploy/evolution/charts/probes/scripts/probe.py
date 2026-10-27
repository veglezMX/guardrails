"""Actual DNS/TCP/TLS probes; expected TLS errors never swallow TCP/DNS failure."""

import getopt
import json
import signal
import socket
import sys
import time
from types import SimpleNamespace

MODES = ("server", "query", "count", "deny", "wrong-ca", "wrong-name", "expired", "dns")
OPTIONS = ("help", "host=", "port=", "ca=", "payload-stdin")
USAGE = "usage: probe.py MODE [--host HOST] [--port INTEGER] [--ca PATH] [--payload-stdin]"


def parse_args(argv):
    try:
        # getopt otherwise consumes a following option as a string value. Keep
        # argparse's missing-value refusal, including unique long abbreviations.
        for index, token in enumerate(argv):
            if token == "--":
                break
            if not token.startswith("--") or "=" in token:
                continue
            matches = [item for item in OPTIONS if item.removesuffix("=").startswith(token[2:])]
            if len(matches) != 1 or not matches[0].endswith("="):
                continue
            if index + 1 == len(argv):
                raise ValueError
            value = argv[index + 1]
            digits = value[1:]
            whole, dot, fraction = digits.partition(".")
            negative_number = digits.isdecimal() or (
                dot and (not whole or whole.isdecimal()) and fraction.isdecimal()
            )
            if value.startswith("-") and value != "-" and not negative_number:
                raise ValueError
        # Python 3.14's explicit in-order mode overrides POSIXLY_CORRECT, so
        # options before and after the one positional mode behave identically.
        options, positional = getopt.gnu_getopt(argv, "-h", OPTIONS)
        args = SimpleNamespace(mode=None, host="probe.gr-platform.svc.cluster.local",
                               port=8443, ca="/secrets/ca.crt", payload_stdin=False)
        modes = []
        for option, value in options:
            if option is None:
                modes.extend(value)
                if len(modes) != 1 or modes[0] not in MODES:
                    raise ValueError
            elif option in ("-h", "--help"):
                print(USAGE + "\nmodes: " + ", ".join(MODES))
                raise SystemExit(0)
            elif option == "--payload-stdin":
                args.payload_stdin = True
            else:
                setattr(args, option[2:], int(value) if option == "--port" else value)
        modes.extend(positional)
        if len(modes) != 1 or modes[0] not in MODES:
            raise ValueError
        args.mode = modes[0]
        return args
    except (getopt.GetoptError, ValueError):
        print(USAGE + "\nprobe.py: invalid arguments", file=sys.stderr)
        raise SystemExit(2) from None


def main():
    args = parse_args(sys.argv[1:])
    if args.mode == "server":
        import http.server
        import ssl
        import threading

        counts = {str(port): 0 for port in (8443, 9443, 8445)}
        mutex = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                with mutex:
                    if self.path != "/count":
                        counts[str(self.server.server_port)] += 1
                    body = json.dumps({"transport": "tls", "counts": counts}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        for port in (8443, 9443, 8445):
            server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(
                "/secrets/expired.crt" if port == 8445 else "/secrets/server.crt",
                "/secrets/server.key",
            )
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        while True:
            time.sleep(60)
    signal.alarm(10)
    addresses = sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(
                args.host, args.port, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
        }
    )
    if not addresses:
        raise RuntimeError("dns_no_addresses")
    if args.mode == "dns":
        print(json.dumps({"result": "resolved", "addresses": addresses}))
        return
    if args.mode == "deny":
        payload = sys.stdin.buffer.read(4097) if args.payload_stdin else b""
        if len(payload) > 4096:
            raise RuntimeError("probe_payload_too_large")
        if args.payload_stdin and not payload:
            raise RuntimeError("probe_payload_missing")
        start = time.monotonic()
        for address in addresses:
            try:
                with socket.create_connection(
                    (address, args.port), timeout=2
                ) as connection:
                    if payload:
                        connection.sendall(payload)
                    raise RuntimeError("unexpected_allowed_connection")
            except TimeoutError:
                pass
        print(
            json.dumps(
                {
                    "result": "denied_timeout",
                    "attempts": len(addresses),
                    "payload_bytes": len(payload),
                    "elapsed_ms": round((time.monotonic() - start) * 1000),
                }
            )
        )
        return
    import ssl

    context = ssl.create_default_context(
        cafile="/secrets/wrong-ca.crt" if args.mode == "wrong-ca" else args.ca
    )
    # TCP must succeed before testing certificate verification failure.
    with socket.create_connection((addresses[0], args.port), timeout=3) as raw:
        try:
            with context.wrap_socket(
                raw,
                server_hostname="wrong.invalid"
                if args.mode == "wrong-name"
                else args.host,
            ) as connection:
                if args.mode in ("wrong-ca", "wrong-name", "expired"):
                    raise RuntimeError("invalid_tls_accepted")
                protocol = connection.version()
                if protocol not in ("TLSv1.2", "TLSv1.3"):
                    raise RuntimeError("unsupported_tls_protocol")
                connection.sendall(
                    (
                        "GET "
                        + ("/count" if args.mode == "count" else "/")
                        + " HTTP/1.1\r\nHost: probe\r\nConnection: close\r\n\r\n"
                    ).encode()
                )
                body = bytearray()
                while chunk := connection.recv(4096):
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise RuntimeError("oversized_probe_response")
                payload = json.loads(body.split(b"\r\n\r\n", 1)[1])
                if b"200 OK" not in body or payload.get("transport") != "tls":
                    raise RuntimeError("invalid_tls_response")
                result = {"result": "tls_verified", "protocol": protocol}
                if args.mode == "count":
                    result["counts"] = payload["counts"]
                print(json.dumps(result))
        except ssl.SSLCertVerificationError as exc:
            if args.mode not in ("wrong-ca", "wrong-name", "expired"):
                raise
            print(
                json.dumps(
                    {"result": "certificate_rejected", "verify_code": exc.verify_code}
                )
            )


if __name__ == "__main__":
    main()
