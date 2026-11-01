"""HTTPS-only administrator client. Credentials never enter shell arguments."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx

from .http_client import BoundedJsonClient
from .models import strict_json_loads

MAX_INPUT = 1024 * 1024


def _secret(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
        ):
            raise ValueError(
                "credential file must be owned by the current user with mode 0600"
            )
        if info.st_size > 4096:
            raise ValueError("credential file is too large")
        value = os.read(fd, 4097).decode().strip()
        if not value or any(c.isspace() for c in value):
            raise ValueError("invalid credential file")
        return value
    finally:
        os.close(fd)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _body(path: str | None):
    if path is None:
        return None
    if path == "-":
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
    else:
        with open(path, "rb") as source:
            data = source.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("input exceeds size limit")
    value = strict_json_loads(data, max_bytes=MAX_INPUT)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


@contextmanager
def _secret_destination(path):
    if path is None:
        yield None
        return
    # Reserve the protected destination before consuming a once-only credential.
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w") as output:
            yield output
    finally:
        try:
            current = os.stat(path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (
                identity.st_dev,
                identity.st_ino,
            ) and current.st_size == 0:
                os.unlink(path)
        except FileNotFoundError:
            pass


def route(args) -> tuple[str, str, dict | None]:
    """Map commands to the public management interface only."""
    segment = lambda value: quote(str(value), safe="")
    tenant = f"/v2/tenants/{segment(args.tenant)}"
    command = tuple(args.command)
    required = {
        ("credential", "issue"): ("id", "file"),
        ("credential", "rotate"): ("id", "file"),
        ("credential", "revoke"): ("id", "file"),
        ("workload", "get"): ("workload",),
        ("workload", "suspend"): ("workload", "expected_revision"),
        ("workload", "manifest"): ("workload",),
        ("workload", "rollback"): ("workload", "file"),
        ("policy", "submit"): ("policy", "file"),
        ("policy", "show"): ("policy", "version"),
        ("policy", "diff"): ("policy", "from_version", "to_version"),
        ("release", "show"): ("id",),
        ("release", "wait"): ("id",),
        ("config", "show"): ("id", "version", "resource"),
        ("config", "create-version"): ("resource", "file"),
        ("evidence", "show"): ("id",),
        ("bundle", "export"): ("id", "file"),
    }.get(command, ())
    if command[:2] == ("policy", "review"):
        required = ("policy", "version", "digest_file", "reason")
    if any(getattr(args, field) is None for field in required):
        raise ValueError("missing command arguments")
    if any(
        getattr(args, field) is not None and getattr(args, field) < 1
        for field in ("version", "from_version", "to_version")
    ):
        raise ValueError("versions must be positive integers")
    body = _body(args.file)
    if command == ("tenant", "create"):
        return "POST", "/v2/tenants", body
    if command == ("tenant", "list"):
        return "GET", "/v2/tenants", None
    if not args.tenant:
        raise ValueError("--tenant is required")
    if command == ("principal", "create"):
        return "POST", tenant + "/principals", body
    if command[:1] == ("credential",):
        if command == ("credential", "issue"):
            return "POST", tenant + f"/principals/{segment(args.id)}/credentials", body
        if command[1:] in (("rotate",), ("revoke",)):
            return (
                "POST",
                tenant + f"/credentials/{segment(args.id)}/{command[1]}",
                body,
            )
    if command[:1] == ("workload",):
        base = tenant + "/workloads"
        if command == ("workload", "register"):
            return "POST", base, body
        if command == ("workload", "list"):
            return "GET", base, None
        base += "/" + segment(args.workload)
        if command == ("workload", "get"):
            return "GET", base, None
        if command == ("workload", "suspend"):
            return "PATCH", base, {"status": "suspended"}
        if command == ("workload", "manifest"):
            return "GET", base + "/manifest", None
        if command == ("workload", "rollback"):
            return "POST", base + "/rollback", body
    if command[:1] == ("policy",):
        base = tenant + "/policies"
        if command == ("policy", "validate"):
            return "POST", base + "/validate", body
        if command == ("policy", "list"):
            return "GET", base, None
        base += "/" + segment(args.policy)
        if command == ("policy", "submit"):
            return "POST", base + "/versions", body
        if command == ("policy", "diff"):
            return (
                "GET",
                base
                + f"/diff?from_version={args.from_version}&to_version={args.to_version}",
                None,
            )
        base += f"/versions/{args.version}"
        if command == ("policy", "show"):
            return "GET", base, None
        if command[:2] == ("policy", "review") and len(command) == 3:
            if command[2] not in ("approve", "reject", "revoke"):
                raise ValueError("unknown review operation")
            digest = Path(args.digest_file).read_text().strip()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid digest file")
            body = {"compiled_digest": digest, "reason": args.reason}
            if command[2] != "revoke":
                body.update(outcome=command[2], evidence_ids=args.evidence_id)
            return (
                "POST",
                base + ("/revoke" if command[2] == "revoke" else "/reviews"),
                body,
            )
    if command[:1] == ("release",):
        base = tenant + "/releases"
        if command[1:] in (("canary",), ("promote",)):
            body = body or {}
            body["stage"] = "canary" if command[1] == "canary" else "full"
            return "POST", base, body
        if command[1:] in (("show",), ("wait",)):
            return "GET", base + "/" + segment(args.id), None
    if command[:1] == ("config",):
        if args.resource not in (
            "lists",
            "residency",
            "destinations",
            "providers",
            "tools",
            "quotas",
        ):
            raise ValueError("unknown configuration resource")
        base = tenant + "/" + args.resource
        if command == ("config", "create-version"):
            if args.id is not None:
                base += f"/{UUID(args.id)}/versions"
            return "POST", base, body
        if command == ("config", "list"):
            return "GET", base, None
        if command == ("config", "show"):
            return "GET", base + f"/{segment(args.id)}/versions/{args.version}", None
    if command in (("audit", "list"), ("audit", "export")):
        return "GET", tenant + "/audit", None
    if command == ("evidence", "show"):
        return "GET", tenant + "/evidence/" + segment(args.id), None
    if command == ("evidence", "submit"):
        if body is None:
            raise ValueError("evidence submission requires --file")
        return "POST", tenant + "/evidence", body
    if command == ("runtime", "register"):
        return "POST", tenant + "/runtimes", body
    if command == ("bundle", "export"):
        return "POST", tenant + f"/runtimes/{segment(args.id)}/bundles", body
    raise ValueError("unsupported command")


def parser():
    result = argparse.ArgumentParser(prog="guardrails-evolution")
    result.add_argument("command", nargs="+")
    for name in (
        "url",
        "ca-file",
        "credential-file",
        "tenant",
        "workload",
        "policy",
        "id",
        "resource",
        "file",
        "digest-file",
        "reason",
        "secret-output",
        "output",
        "cursor",
        "action",
        "from",
        "until",
    ):
        result.add_argument("--" + name)
    for name in ("version", "from-version", "to-version", "expected-revision"):
        result.add_argument("--" + name, type=int)
    result.add_argument("--evidence-id", action="append", default=[])
    result.add_argument("--idempotency-key")
    result.add_argument("--timeout", type=float, default=30)
    result.add_argument("--limit", type=int, default=50)
    result.add_argument("--json", action="store_true")
    return result


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        parsed = urlsplit(args.url or "")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("--url must be an HTTPS service origin")
        if not args.ca_file or not args.credential_file or not 0 < args.timeout <= 600:
            raise ValueError(
                "CA file, credential file and bounded timeout are required"
            )
        method, path, body = route(args)
        query = {}
        if method == "GET" and (
            args.command[-1] == "list" or args.command == ["audit", "export"]
        ):
            if not 1 <= args.limit <= 100:
                raise ValueError("--limit must be between 1 and 100")
            query["limit"] = args.limit
            if args.cursor:
                query["cursor"] = args.cursor
            if args.command[0] == "audit":
                query.update(
                    {
                        key: value
                        for key, value in {
                            "workload_id": args.workload,
                            "action": args.action,
                            "from": getattr(args, "from"),
                            "until": args.until,
                        }.items()
                        if value is not None
                    }
                )
        headers = {
            "Authorization": "Bearer " + _secret(args.credential_file),
            "X-Request-ID": str(uuid4()),
        }
        if method != "GET":
            headers["Idempotency-Key"] = (
                str(UUID(args.idempotency_key))
                if args.idempotency_key
                else str(uuid4())
            )
        if args.expected_revision is not None:
            headers["If-Match"] = str(args.expected_revision)
        issuing = tuple(args.command) in (
            ("credential", "issue"),
            ("credential", "rotate"),
        )
        if issuing and (not args.secret_output or Path(args.secret_output).exists()):
            raise ValueError("credential issuance requires a new --secret-output file")
        import ssl

        deadline = time.monotonic() + args.timeout
        context = ssl.create_default_context(cafile=args.ca_file)
        with (
            _secret_destination(args.secret_output if issuing else None) as secret_file,
            BoundedJsonClient(
                base_url=args.url.rstrip("/"),
                verify=context,
                deadline=deadline,
            ) as client,
        ):
            exported, seen_cursors = [], set()
            while True:
                if time.monotonic() >= deadline:
                    print("Management request deadline exceeded", file=sys.stderr)
                    return 5
                response = client.request(
                    method,
                    path,
                    params=query or None,
                    json=body,
                    headers=headers,
                    timeout=max(0.01, deadline - time.monotonic()),
                )
                if response.status_code >= 300:
                    # Never print upstream error bodies: a failed intermediary may echo secrets.
                    print(
                        f"Management request failed (HTTP {response.status_code})",
                        file=sys.stderr,
                    )
                    return (
                        3
                        if response.status_code in (401, 403)
                        else 4
                        if response.status_code in (409, 428)
                        else 5
                        if response.status_code >= 500
                        else 2
                    )
                result = strict_json_loads(response.content, max_bytes=8 * MAX_INPUT)
                if tuple(args.command) == ("audit", "export"):
                    exported.extend(result["items"])
                    if len(exported) > 100000:
                        raise ValueError("audit export exceeds bounded record limit")
                    cursor = result["next_cursor"]
                    if cursor is not None:
                        if not isinstance(cursor, str) or cursor in seen_cursors:
                            raise ValueError("invalid pagination response")
                        seen_cursors.add(cursor)
                        query["cursor"] = cursor
                        continue
                    result = {"items": exported, "next_cursor": None}
                if tuple(args.command) != ("release", "wait"):
                    break
                release = result.get("release", result)
                if release.get("status") == "applied":
                    break
                if release.get("status") == "failed" or time.monotonic() >= deadline:
                    print("Release did not become applied", file=sys.stderr)
                    return 5
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if issuing:
                secret_file.write(result.pop("one_time_secret") + "\n")
                secret_file.flush()
                os.fsync(secret_file.fileno())
            rendered = (
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
            if args.output:
                fd = os.open(
                    args.output,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w") as output:
                    output.write(rendered)
            else:
                if args.json:
                    print(rendered, end="")
                elif isinstance(result, dict) and "items" in result:
                    print(f"{len(result['items'])} records")
                    for item in result["items"]:
                        print(_summary(item))
                    if result.get("next_cursor"):
                        print("Next cursor: " + result["next_cursor"])
                else:
                    print(_summary(result))
        return 0
    except httpx.HTTPError:
        print(
            "Management service unavailable or TLS verification failed", file=sys.stderr
        )
        return 5
    except OSError, ValueError, TypeError, KeyError:
        print(
            "Request failed; check command arguments, protected input files and service availability",
            file=sys.stderr,
        )
        return 2


def _summary(record):
    """Human output contains only identifiers, finite state and version values."""
    if not isinstance(record, dict):
        return "Request completed"
    fields = (
        "tenant_id",
        "workload_id",
        "policy_id",
        "version",
        "principal_id",
        "credential_id",
        "release_id",
        "run_id",
        "event_id",
        "status",
        "action",
    )
    return (
        " ".join(f"{key}={record[key]}" for key in fields if key in record)
        or "Request completed"
    )


if __name__ == "__main__":
    raise SystemExit(main())
