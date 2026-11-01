"""Resume bounded task-owned credential renewal without rewriting seed history.

This is an operator maintenance command, not an alternate authentication API.
Secrets live only in exclusive private token files and existing Kubernetes Secrets.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import math
import os
import ssl
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from evolution import seed
from guardrails.evolution.bindings import BindingService
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.models import (
    canonical_json_bytes,
    parse_time,
    strict_json_loads,
)
from guardrails.evolution.storage import TenantRepository

OWNER = "guardrails.evolution/owner"
REVISION = "guardrails.evolution/config-revision"
ROLES = ("tenant_admin", "author", "approver", "operator", "auditor", "workload", "runtime-1", "runtime-2")
HISTORY = ("seed-spec.json", "seed/bootstrap.json", "seed/bootstrap-plan.json", "seed/operations.json", "seed/inventory.json")
MAX_RECORD = 1024 * 1024
CONSUMERS = ("runtime-1", "runtime-2", "gateway-team-a", "gateway-team-b")


class CredentialRenewalError(RuntimeError):
    """Only fixed failure codes may reach the command boundary."""


def fail(code="credential_renewal_invalid"):
    raise CredentialRenewalError(code)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical_json_bytes(value)).hexdigest()


def _uuid(value):
    try:
        valid = type(value) is str and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        fail()
    return value


def _directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    return _check_directory(path)


def _check_directory(path):
    path = Path(path)
    info = path.lstat()
    if path != path.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        fail("credential_directory_not_private")
    return path


def private_read(path, *, maximum=MAX_RECORD):
    """Never block opening a FIFO/device before checking its actual file type."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        details = os.fstat(source.fileno())
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or details.st_mode & 0o077 or details.st_size > maximum):
            fail("credential_private_file_invalid")
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        fail("credential_private_file_invalid")
    return raw


def _read(path):
    return strict_json_loads(private_read(path, maximum=MAX_RECORD), max_bytes=MAX_RECORD)


def _write(path, value):
    raw = canonical_json_bytes(value)
    if len(raw) > MAX_RECORD:
        fail("credential_journal_limit")
    path = Path(path)
    if path.exists() or path.is_symlink():
        private_read(path, maximum=MAX_RECORD)
        seed.private_write(path, raw, replace=True)
        return
    staged = path.with_name(path.name + "." + uuid4().hex + ".staged")
    seed.private_write(staged, raw)
    try:
        # link is exclusive: no torn first publication and no overwrite on collision.
        os.link(staged, path, follow_symlinks=False)
    finally:
        staged.unlink()
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _history(directory):
    return {name: None if name == "seed/bootstrap-plan.json" and not (directory / name).exists() and not (directory / name).is_symlink()
            else digest(private_read(directory / name)) for name in HISTORY}


def _base(directory):
    return Path(directory) / "credential-renewals"


def _token(path, directory):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve() or not path.is_relative_to(directory):
        fail("credential_path_invalid")
    return private_read(path, maximum=4096).decode().strip()


def _token_id(path, directory, scope):
    parts = _token(path, directory).split(".")
    if len(parts) != 4 or parts[:2] != ["gr2", scope] or len(parts[3]) != 43:
        fail("credential_token_invalid")
    return _uuid(parts[2])


def _historical_slots(directory):
    inventory = _read(directory / "seed/inventory.json")
    bootstrap = _read(directory / "seed/bootstrap.json")
    if set(inventory["tenants"]) != {"team-a", "team-b"}:
        fail("credential_identity_changed")
    platform_file = bootstrap["platform_token_file"]
    slots = {"platform": {"scope": "_platform", "principal_id": bootstrap["platform_principal_id"],
                          "roles": ["platform_admin"], "workload_id": None,
                          "old_file": platform_file,
                          "old_credential_id": _token_id(platform_file, directory, "_platform")}}
    for tenant, value in inventory["tenants"].items():
        if set(value["roles"]) != set(ROLES):
            fail("credential_identity_changed")
        for role, row in value["roles"].items():
            if _token_id(row["token_file"], directory, tenant) != row["credential_id"]:
                fail("credential_identity_changed")
            slots[tenant + "/" + role] = {
                "scope": tenant, "principal_id": row["principal_id"],
                "roles": ["runtime" if role.startswith("runtime-") else role],
                "workload_id": value["workload"]["workload_id"] if role == "workload" else None,
                "old_file": row["token_file"], "old_credential_id": row["credential_id"],
            }
    return slots


def _journal(directory, generation, owner, *, seen=()):
    generation = _uuid(generation)
    if generation in seen or len(seen) >= 100:
        fail("credential_history_invalid")
    _check_directory(_base(directory))
    path = _check_directory(_base(directory) / generation) / "journal.json"
    value = _read(path)
    if (type(value) is not dict or set(value) != {
            "schema_version", "generation", "owner_id", "node_id", "history", "expires_at",
            "previous", "status", "entries", "rollouts", "metadata", "created_at",
            "bindings",
        } or value["schema_version"] != "task-credential-renewal-v1"
            or value["generation"] != generation or value["owner_id"] != owner["owner_id"]
            or value["history"] != _history(directory)
            or value["status"] not in {"issuing", "rolling", "retiring", "complete"}
            or type(value["entries"]) is not list or len(value["entries"]) != 17
            or type(value["rollouts"]) is not dict or type(value["metadata"]) is not dict):
        fail("credential_journal_invalid")
    parse_time(value["expires_at"])
    parse_time(value["created_at"])
    if type(value["node_id"]) is not str or not value["node_id"]:
        fail("credential_journal_invalid")
    slots = _historical_slots(directory)
    inventory = _read(directory / "seed/inventory.json")
    if type(value["bindings"]) is not dict or set(value["bindings"]) != {"team-a", "team-b"}:
        fail("credential_journal_invalid")
    for tenant, binding in value["bindings"].items():
        if (type(binding) is not dict or set(binding) != {"workload_id", "sha256"}
                or binding["workload_id"] != inventory["tenants"][tenant]["workload"]["workload_id"]
                or type(binding["sha256"]) is not str or len(binding["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in binding["sha256"])):
            fail("credential_journal_invalid")
    if value["previous"] is not None:
        _, previous = _journal(directory, value["previous"], owner, seen=(*seen, generation))
        if previous["status"] != "complete":
            fail("credential_history_invalid")
        for entry in previous["entries"]:
            slots[entry["slot"]].update(old_file=entry["file"], old_credential_id=entry["receipt"]["credential_id"])
    keys = set()
    for entry in value["entries"]:
        if (type(entry) is not dict or set(entry) != {
                "slot", "scope", "principal_id", "workload_id", "old_credential_id", "old_file",
                "roles", "key", "file", "receipt", "retired",
                "orphan",
                "token_sha256", "old_sha256",
            } or type(entry["slot"]) is not str or entry["slot"] not in slots
                or entry["slot"] in keys or type(entry["retired"]) is not bool
                or any(entry[key] != expected for key, expected in slots[entry["slot"]].items())):
            fail("credential_journal_invalid")
        keys.add(entry["slot"])
        _uuid(entry["principal_id"])
        _uuid(entry["old_credential_id"])
        _uuid(entry["key"])
        if digest(private_read(entry["old_file"], maximum=4096)) != entry["old_sha256"]:
            fail("credential_identity_changed")
        stem = entry["slot"].replace("/", "-")
        orphan = entry["orphan"]
        if orphan is not None:
            if (type(orphan) is not dict or set(orphan) != {"credential_id", "key", "file", "expires_at", "revoked"}
                    or orphan["expires_at"] != value["expires_at"] or type(orphan["revoked"]) is not bool
                    or orphan["file"] != str(_base(directory) / generation / (stem + ".token"))
                    or orphan["key"] == entry["key"]):
                fail("credential_journal_invalid")
            _uuid(orphan["credential_id"])
            _uuid(orphan["key"])
            if entry["scope"] == "_platform" and orphan["revoked"]:
                fail("credential_journal_invalid")
            if value["status"] != "issuing" and entry["scope"] != "_platform" and not orphan["revoked"]:
                fail("credential_journal_invalid")
        expected = _base(directory) / generation / (stem + ("-replacement" if orphan else "") + ".token")
        if entry["file"] != str(expected):
            fail("credential_path_invalid")
        if entry["receipt"] is not None:
            receipt = entry["receipt"]
            if (type(receipt) is not dict or set(receipt) != {"credential_id", "expires_at"}
                    or receipt["expires_at"] != value["expires_at"]):
                fail("credential_journal_invalid")
            _uuid(receipt["credential_id"])
            if _token_id(entry["file"], directory, entry["scope"]) != receipt["credential_id"]:
                fail("credential_identity_changed")
            if digest(private_read(entry["file"], maximum=4096)) != entry["token_sha256"]:
                fail("credential_identity_changed")
        elif entry["token_sha256"] is not None:
            fail("credential_journal_invalid")
        if value["status"] != "issuing" and entry["receipt"] is None:
            fail("credential_journal_invalid")
        if entry["scope"] == "_platform" and entry["retired"]:
            fail("credential_journal_invalid")
    expected = {"platform"} | {tenant + "/" + role for tenant in ("team-a", "team-b") for role in ROLES}
    if keys != expected:
        fail("credential_journal_invalid")
    if value["status"] == "issuing":
        if value["rollouts"] or value["metadata"] or any(e["retired"] for e in value["entries"]):
            fail("credential_journal_invalid")
    else:
        _validate_transition(value, directory)
    return path, value


def _changes(journal, name):
    entries = {entry["slot"]: entry for entry in journal["entries"]}
    return ({tenant + ".token": entries[tenant + "/" + name] for tenant in ("team-a", "team-b")}
            if name.startswith("runtime-") else {"workload.token": entries[name.removeprefix("gateway-") + "/workload"]})


def _validate_transition(journal, directory):
    if set(journal["metadata"]) != {"access.json", "values.json"} or set(journal["rollouts"]) != set(CONSUMERS):
        fail("credential_journal_invalid")
    before, after = {}, {}
    for name, pair in journal["metadata"].items():
        if type(pair) is not dict or set(pair) != {"before", "after"}:
            fail("credential_journal_invalid")
        before[name], after[name] = [strict_json_loads(base64.b64decode(pair[k], validate=True), max_bytes=MAX_RECORD) for k in ("before", "after")]
    expected_access = copy.deepcopy(before["access.json"])
    expected_values = copy.deepcopy(before["values.json"])
    for entry in journal["entries"]:
        if entry["slot"].endswith("/workload"):
            if expected_access["tenants"][entry["scope"]]["workload_token_file"] != entry["old_file"]:
                fail("credential_journal_invalid")
            expected_access["tenants"][entry["scope"]]["workload_token_file"] = entry["file"]
    for name, plan in journal["rollouts"].items():
        if type(plan) is not dict or set(plan) != {
            "namespace", "secret_uid", "deployment_uid", "template_sha256", "before", "after",
            "old_revision", "new_revision", "files", "started_at", "verified_at", "complete",
        } or type(plan["complete"]) is not bool:
            fail("credential_journal_invalid")
        namespace = "gr-platform" if name.startswith("runtime-") else "gr-" + name.removeprefix("gateway-")
        if plan["namespace"] != namespace or any(type(plan[k]) is not str or not plan[k] for k in ("secret_uid", "deployment_uid")):
            fail("credential_journal_invalid")
        for key in ("template_sha256", "old_revision", "new_revision"):
            if type(plan[key]) is not str or len(plan[key]) != 64 or any(c not in "0123456789abcdef" for c in plan[key]):
                fail("credential_journal_invalid")
        if plan["started_at"] is not None:
            parse_time(plan["started_at"])
        elif plan["complete"]:
            fail("credential_journal_invalid")
        if plan["verified_at"] is not None:
            if plan["started_at"] is None or parse_time(plan["verified_at"]) < parse_time(plan["started_at"]):
                fail("credential_journal_invalid")
        elif plan["complete"]:
            fail("credential_journal_invalid")
        changes = _changes(journal, name)
        if plan["files"] != {key: entry["file"] for key, entry in changes.items()} or type(plan["before"]) is not dict:
            fail("credential_journal_invalid")
        planned = dict(plan["before"])
        for key, entry in changes.items():
            if planned.get(key) != digest(private_read(entry["old_file"], maximum=4096)):
                fail("credential_journal_invalid")
            planned[key] = digest(private_read(entry["file"], maximum=4096))
        if planned != plan["after"] or any(type(v) is not str or len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in planned.values()):
            fail("credential_journal_invalid")
        row = expected_values["components"][name]
        if row["namespace"] != namespace or row["configRevision"] != plan["old_revision"]:
            fail("credential_journal_invalid")
        row["configRevision"] = plan["new_revision"]
    if expected_access != after["access.json"] or expected_values != after["values.json"]:
        fail("credential_journal_invalid")
    if journal["status"] in {"retiring", "complete"} and not all(p["complete"] for p in journal["rollouts"].values()):
        fail("credential_journal_invalid")
    if journal["status"] == "complete" and not all(e["retired"] for e in journal["entries"] if e["scope"] != "_platform"):
        fail("credential_journal_invalid")


def ensure_available(directory, owner):
    """Direct overlay consumers also refuse an interrupted mixed generation."""
    base = _base(directory)
    if base.exists() or base.is_symlink():
        _check_directory(base)
    pending = _base(directory) / "pending.json"
    if pending.exists() or pending.is_symlink():
        fail("credential_renewal_pending")
    path = _base(directory) / "active.json"
    if not path.exists() and not path.is_symlink():
        return None
    pointer = _read(path)
    if type(pointer) is not dict or set(pointer) != {"generation", "sha256"}:
        fail("credential_overlay_invalid")
    journal_path, journal = _journal(directory, pointer["generation"], owner)
    if journal["status"] != "complete" or digest(private_read(journal_path)) != pointer["sha256"]:
        fail("credential_overlay_invalid")
    access = _read(Path(directory) / "access.json")
    for entry in journal["entries"]:
        if entry["slot"].endswith("/workload") and access["tenants"][entry["scope"]]["workload_token_file"] != entry["file"]:
            fail("credential_access_changed")
    return journal


def _active(directory, owner):
    return ensure_available(directory, owner)


def _replace_inventory(inventory, journal, directory):
    result = copy.deepcopy(inventory)
    for entry in journal["entries"]:
        if entry["slot"] == "platform":
            continue
        tenant, role = entry["slot"].split("/")
        current = result["tenants"][tenant]["roles"][role]
        if current["principal_id"] != entry["principal_id"] or entry["receipt"] is None:
            fail("credential_identity_changed")
        _token(entry["file"], directory)
        current.update(entry["receipt"], token_file=entry["file"])
        if role.startswith("runtime-"):
            runtime = result["tenants"][tenant]["runtimes"][role]
            if runtime["principal_id"] != entry["principal_id"]:
                fail("credential_identity_changed")
            runtime.update(entry["receipt"], token_file=entry["file"])
    return result


def resolve_inventory(directory, owner, inventory):
    directory = Path(directory)
    if inventory != _read(directory / "seed/inventory.json"):
        fail("credential_inventory_changed")
    journal = _active(directory, owner)
    return _replace_inventory(inventory, journal, directory) if journal else copy.deepcopy(inventory)


def retained_token_files(directory, owner):
    """Original caller inventory remains included; additionally retain all renewals."""
    journal = _active(Path(directory), owner)
    result = set()
    seen = set()
    while journal:
        if journal["generation"] in seen or len(seen) >= 100:
            fail("credential_history_invalid")
        seen.add(journal["generation"])
        result.update(entry["file"] for entry in journal["entries"])
        result.update(entry["orphan"]["file"] for entry in journal["entries"] if entry["orphan"] is not None)
        if journal["previous"] is None:
            break
        _, journal = _journal(Path(directory), journal["previous"], owner)
        if journal["status"] != "complete":
            fail("credential_history_invalid")
    return result


def describe_inventory(directory, owner, inventory):
    """Resolve one private generation; all paths are for privacy checks, never output."""
    directory = Path(directory)
    if inventory != _read(directory / "seed/inventory.json"):
        fail("credential_inventory_changed")
    journal = _active(directory, owner)
    paths = {entry["old_file"] for entry in _historical_slots(directory).values()}
    paths.update(value["gateway_token_file"] for value in inventory["tenants"].values())
    paths.update(retained_token_files(directory, owner))
    for path in paths:
        _token(path, directory)
    return {"inventory": _replace_inventory(inventory, journal, directory) if journal else copy.deepcopy(inventory),
            "generation": journal["generation"] if journal else None,
            "sha256": digest(private_read(_base(directory) / journal["generation"] / "journal.json")) if journal else None,
            "token_files": tuple(sorted(paths))}


class Renewal:
    def __init__(self, deployment, directory, owner, *, deadline=None):
        self.deployment, self.directory, self.owner = deployment, directory, owner
        self.assembly = deployment.support("assembly")
        self.deadline = deadline or time.monotonic() + 1200
        self.access = _read(directory / "access.json")
        self.inventory = _read(directory / "seed/inventory.json")
        ports = self.assembly._port_record(deployment, directory)
        if (self.access["management_url"] != f"https://localhost:{ports['management']}"
                or self.access["ca_file"] != str(deployment.state / "credentials/ca.crt")
                or self.access["seed_inventory_file"] != str(directory / "seed/inventory.json")
                or self.access["database_url_file"] != str(directory / "private/database-app-url")):
            fail("credential_endpoint_changed")
        self.repository = TenantRepository(_token(self.access["database_url_file"], directory))
        self.identity = IdentityService(self.repository)
        self.last_request = 0.0

    def close(self):
        self.repository.close()

    def remaining(self, maximum=30):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            fail("credential_renewal_timeout")
        return min(remaining, maximum)

    @contextlib.contextmanager
    def db(self):
        token = current_deadline.set(time.monotonic() + self.remaining(5))
        try:
            yield
        finally:
            current_deadline.reset(token)

    def authenticate(self, filename, entry=None):
        with self.db():
            identity = self.identity.authenticate(_token(filename, self.directory))
        if entry and (identity.scope != entry["scope"] or identity.principal_id != entry["principal_id"]
                      or identity.workload_id != entry["workload_id"] or list(identity.roles) != entry["roles"]):
            fail("credential_identity_changed")
        return identity

    def owner_unchanged(self):
        current = self.deployment.owner()
        if any(current.get(key) != self.owner.get(key) for key in ("owner_id", "container_id")):
            fail("credential_pending_node_changed")
        code, raw = self.deployment.run(["docker", "container", "inspect", "guardrails-evolution"],
                                         check=False, timeout=self.remaining(5))
        rows = strict_json_loads(raw, max_bytes=MAX_RECORD) if code == 0 else []
        if (type(rows) is not list or len(rows) != 1 or rows[0].get("Id") != self.owner["container_id"]
                or rows[0].get("Name") != "/guardrails-evolution" or rows[0].get("State", {}).get("Running") is not True):
            fail("credential_node_not_running")

    def request(self, method, path, filename, *, body=None, key=None, expected=200):
        # The private operator transport owns an absolute bound, no retry, and
        # discards every error body. It never prints command/HTTP exceptions.
        import asyncio

        import httpx
        delay = max(0, .13 - (time.monotonic() - self.last_request))
        if delay:
            time.sleep(min(delay, self.remaining(delay)))
        self.last_request = time.monotonic()
        token = _token(filename, self.directory)
        async def fetch():
            async with asyncio.timeout(self.remaining(5)):
                async with httpx.AsyncClient(base_url=self.access["management_url"],
                        verify=ssl.create_default_context(cafile=self.access["ca_file"]),
                        trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(5, connect=.25)) as client:
                    headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}
                    if key:
                        headers["Idempotency-Key"] = key
                    async with client.stream(method, path, headers=headers, json=body) as response:
                        if response.status_code != expected:
                            fail("credential_http_rejected")
                        if response.headers.get("content-encoding", "identity") != "identity":
                            fail("credential_http_invalid")
                        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                            fail("credential_http_invalid")
                        raw = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(raw) + len(chunk) > 32768:
                                fail("credential_http_invalid")
                            raw.extend(chunk)
                        return strict_json_loads(raw, max_bytes=32768)
        return asyncio.run(fetch())

    def entries(self, active):
        source = _replace_inventory(self.inventory, active, self.directory) if active else self.inventory
        bootstrap = _read(self.directory / "seed/bootstrap.json")
        platform = next((e for e in active["entries"] if e["slot"] == "platform"), None) if active else None
        platform_file = platform["file"] if platform else bootstrap["platform_token_file"]
        actor = self.authenticate(platform_file)
        if actor.scope != "_platform" or actor.roles != ("platform_admin",) or actor.principal_id != bootstrap["platform_principal_id"]:
            fail("credential_identity_changed")
        selected = [("platform", platform_file, actor)]
        for tenant in ("team-a", "team-b"):
            for role in ROLES:
                row = source["tenants"][tenant]["roles"][role]
                identity = self.authenticate(row["token_file"])
                expected_role = "runtime" if role.startswith("runtime-") else role
                if (identity.principal_id != row["principal_id"] or identity.scope != tenant
                        or identity.roles != (expected_role,)
                        or identity.workload_id != (source["tenants"][tenant]["workload"]["workload_id"] if role == "workload" else None)):
                    fail("credential_identity_changed")
                selected.append((tenant + "/" + role, row["token_file"], identity))
        return [{"slot": slot, "scope": who.scope, "principal_id": who.principal_id,
                 "workload_id": who.workload_id, "old_credential_id": who.credential["credential_id"],
                 "old_file": filename, "roles": list(who.roles), "key": str(uuid4()),
                 "file": "", "receipt": None, "retired": False, "orphan": None,
                 "token_sha256": None, "old_sha256": digest(private_read(filename, maximum=4096))} for slot, filename, who in selected]

    @staticmethod
    def binding_state(tx, workload_id):
        desired = tx.get("binding_pointers", workload_id, required=True)
        applied = tx.get("applied_bindings", workload_id, required=True)
        binding = tx.get("bindings", f"{workload_id}:{desired['binding_revision']}", required=True)
        if applied["binding_revision"] != desired["binding_revision"] or applied["policy"] != binding["policy"]:
            fail("credential_binding_not_applied")
        return {"desired": desired, "applied": applied, "binding": binding}

    def capture_bindings(self):
        result = {}
        for tenant, row in self.inventory["tenants"].items():
            workload_id = row["workload"]["workload_id"]
            with self.db(), self.repository.transaction(tenant) as tx:
                tx.lock("governance-admission")
                result[tenant] = {"workload_id": workload_id, "sha256": digest(self.binding_state(tx, workload_id))}
        return result

    def _retire_orphan(self, journal, entry, save):
        orphan = entry["orphan"]
        if orphan is None or orphan["revoked"] or entry["scope"] == "_platform":
            return
        admin = next(e for e in journal["entries"] if e["slot"] == entry["scope"] + "/tenant_admin")
        authority = admin["file"] if admin["receipt"] else admin["old_file"]
        self.request("POST", f"/v2/tenants/{entry['scope']}/credentials/{orphan['credential_id']}/revoke",
                     authority, body={"reason": "Lost task renewal response"}, key=orphan["key"])
        orphan["revoked"] = True
        save()

    def _recover_lost(self, journal, entry, receipt, save):
        if entry["orphan"] is not None or entry["receipt"] is not None:
            fail("credential_secret_lost")
        response = receipt.get("response")
        if type(response) is not dict or set(response) != {"credential_id", "expires_at"} or response["expires_at"] != journal["expires_at"]:
            fail("credential_receipt_mismatch")
        _uuid(response["credential_id"])
        with self.db(), self.repository.transaction(entry["scope"]) as tx:
            credential = tx.get("credentials", response["credential_id"], required=True)
        if (credential["principal_id"] != entry["principal_id"] or credential["workload_id"] != entry["workload_id"]
                or credential["expires_at"] != journal["expires_at"]
                or credential["tenant_id"] != (None if entry["scope"] == "_platform" else entry["scope"])):
            fail("credential_receipt_mismatch")
        entry["orphan"] = {**response, "key": entry["key"], "file": entry["file"], "revoked": False}
        entry["key"] = str(uuid4())
        entry["file"] = str(Path(entry["file"]).with_name(entry["slot"].replace("/", "-") + "-replacement.token"))
        save()  # Publish the single replacement intent before any revocation/issuance.
        self._retire_orphan(journal, entry, save)

    def issue(self, journal, save, *, recover_lost=False):
        platform = next(e for e in journal["entries"] if e["slot"] == "platform")
        for entry in journal["entries"]:
            self._retire_orphan(journal, entry, save)
            path = f"/v2/tenants/{entry['scope']}/principals/{entry['principal_id']}/credentials"
            body = {"expires_at": journal["expires_at"]}
            if entry["workload_id"] is not None:
                body["workload_id"] = entry["workload_id"]
            authority = (platform["file"] if Path(platform["file"]).exists()
                         and _token(platform["file"], self.directory) else platform["old_file"])
            actor = self.authenticate(authority, platform)
            idem = f"{actor.principal_id}:POST:{path}:{entry['key']}"
            filename = Path(entry["file"])
            if not filename.exists():
                seed.private_write(filename, b"")
            secret = _token(filename, self.directory)
            with self.db(), self.repository.transaction(entry["scope"]) as tx:
                receipt = tx.get("idempotency", idem)
            if receipt:
                if (receipt.get("fingerprint") != digest(body) or receipt.get("secret_delivered") is not True
                        or receipt.get("status") != 201):
                    fail("credential_receipt_mismatch")
                if not secret:
                    if recover_lost:
                        self._recover_lost(journal, entry, receipt, save)
                        return self.issue(journal, save, recover_lost=recover_lost)
                    fail("credential_secret_lost")
                issued = self.authenticate(filename, entry)
                expected = {"credential_id": issued.credential["credential_id"], "expires_at": issued.credential["expires_at"]}
                if receipt.get("response") != expected or expected["expires_at"] != journal["expires_at"]:
                    fail("credential_receipt_mismatch")
            elif entry["receipt"] is not None or secret:
                fail("credential_receipt_missing")
            else:
                result = self.request("POST", path, authority, body=body, key=entry["key"], expected=201)
                if (type(result) is not dict or set(result) != {"credential_id", "expires_at", "one_time_secret"}
                        or result["expires_at"] != journal["expires_at"]
                        or type(result["one_time_secret"]) is not str or len(result["one_time_secret"]) > 256):
                    fail("credential_http_invalid")
                _uuid(result["credential_id"])
                seed.private_write(filename, result["one_time_secret"].encode(), replace=True)
                issued = self.authenticate(filename, entry)
                expected = {"credential_id": issued.credential["credential_id"], "expires_at": issued.credential["expires_at"]}
                if expected != {k: result[k] for k in expected}:
                    fail("credential_receipt_mismatch")
                with self.db(), self.repository.transaction(entry["scope"]) as tx:
                    receipt = tx.get("idempotency", idem)
                if (not receipt or receipt.get("fingerprint") != digest(body)
                        or receipt.get("secret_delivered") is not True or receipt.get("status") != 201
                        or receipt.get("response") != expected):
                    fail("credential_receipt_mismatch")
            if entry["receipt"] not in (None, expected):
                fail("credential_receipt_mismatch")
            entry["receipt"] = expected
            entry["token_sha256"] = digest(private_read(filename, maximum=4096))
            save()

    def get(self, kind, name, namespace):
        code, raw = self.deployment.kubectl("get", kind, name, "-o", "json", namespace=namespace,
                                        timeout=self.remaining())
        if code:
            fail("credential_kubernetes_rejected")
        value = strict_json_loads(raw)
        meta = value["metadata"]
        if (meta.get("name") != name or meta.get("namespace") != namespace
                or meta.get("labels", {}).get(OWNER) != self.owner["owner_id"]
                or not meta.get("uid") or not meta.get("resourceVersion")):
            fail("credential_resource_not_owned")
        return value

    def patch(self, kind, name, namespace, value, operations):
        self.owner_unchanged()
        meta = value["metadata"]
        tests = [{"op": "test", "path": "/metadata/" + key, "value": meta[key]}
                 for key in ("uid", "resourceVersion", "labels")]
        code, _ = self.deployment.kubectl("patch", kind, name, "--type=json", "--patch-file=/dev/stdin",
                               namespace=namespace, input_text=json.dumps(tests + operations),
                               timeout=self.remaining())
        if code:
            fail("credential_kubernetes_rejected")

    @staticmethod
    def template_hash(target):
        template = copy.deepcopy(target["spec"]["template"])
        template["metadata"]["annotations"].pop(REVISION, None)
        return digest(template)

    @staticmethod
    def data_hashes(secret):
        if secret.get("type") != "Opaque" or type(secret.get("data")) is not dict:
            fail("credential_secret_invalid")
        return {name: digest(base64.b64decode(value, validate=True)) for name, value in secret["data"].items()}

    def verify_consumers(self, values):
        """Use the production immutable image/rootfs proof for the four consumers."""
        process = self
        class BoundedDeployment:
            def __getattr__(self, name):
                return getattr(process.deployment, name)

            def kubectl(self, *args, **kwargs):
                kwargs["timeout"] = process.remaining(kwargs.get("timeout", 30))
                return process.deployment.kubectl(*args, **kwargs)

            def run(self, *args, **kwargs):
                kwargs["timeout"] = process.remaining(kwargs.get("timeout", 30))
                return process.deployment.run(*args, **kwargs)
        self.owner_unchanged()
        subset = {**values, "components": {name: values["components"][name] for name in CONSUMERS}}
        self.assembly._verify_application_images(BoundedDeployment(), subset, self.owner)
        self.remaining()

    def validate_target(self, name, row, values, target):
        template = target["spec"]["template"]
        spec = template["spec"]
        app, = spec["containers"]
        initial, = spec["initContainers"]
        security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
        expected_security = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                             "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
        chart = self.deployment.root / "deploy/evolution/charts/platform/templates/workloads.yaml"
        program_lines = [line.strip()[2:] for line in chart.read_text().splitlines()
                         if line.strip().startswith('- "import os,pathlib;')]
        if len(program_lines) != 1:
            fail("credential_initializer_changed")
        program = json.loads(program_lines[0])
        if (row["namespace"] != ("gr-platform" if name.startswith("runtime-") else "gr-" + name.removeprefix("gateway-"))
                or not row["enabled"] or target["spec"].get("replicas") != 1
                or target["spec"].get("strategy") != {"type": "Recreate"}
                or spec.get("serviceAccountName") != name or spec.get("automountServiceAccountToken") is not False
                or any(spec.get(k) for k in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace", "ephemeralContainers", "hostAliases"))
                or spec.get("securityContext") != expected_security
                or app.get("name") != row["component"] or app.get("image") != row["image"]
                or app.get("imagePullPolicy") != "Never" or app.get("command", []) != (row.get("command") or [])
                or app.get("args", []) != (row.get("args") or []) or app.get("securityContext") != security
                or {e.get("name"): e.get("value") for e in app.get("env", [])} != row.get("env", {})
                or any(set(e) != {"name", "value"} for e in app.get("env", []))
                or len(app.get("env", [])) != len(row.get("env", {}))
                or app.get("envFrom") or initial.get("env") or initial.get("envFrom")
                or app.get("lifecycle") or initial.get("lifecycle") or initial.get("restartPolicy")
                or not self.assembly._same_limits(app.get("resources", {}).get("limits", {}), row["resources"]["limits"])
                or not self.assembly._same_limits(app.get("resources", {}).get("requests", {}), row["resources"]["requests"])
                or initial.get("name") != "private-files" or initial.get("image") != values["initImage"]
                or initial.get("imagePullPolicy") != "Never" or initial.get("securityContext") != security
                or initial.get("command") != ["python", "-B", "-c"]
                or initial.get("args") != [program]
                or initial.get("volumeMounts") != [{"name": "projected", "mountPath": "/projected", "readOnly": True},
                                                  {"name": "private", "mountPath": "/private"}, {"name": "state", "mountPath": "/state"}]
                or not self.assembly._same_limits(initial.get("resources", {}).get("limits", {}), {"cpu": "100m", "memory": "32Mi"})
                or not self.assembly._same_limits(initial.get("resources", {}).get("requests", {}), {"cpu": "10m", "memory": "16Mi"})
                or app.get("volumeMounts") != [{"name": "private", "mountPath": "/config", "subPath": "data", "readOnly": True},
                                              {"name": "state", "mountPath": "/state", "subPath": "data"}, {"name": "temporary", "mountPath": "/tmp"}]
                or spec.get("volumes") != [{"name": "projected", "secret": {"secretName": name + "-config", "defaultMode": 288}},
                                           {"name": "private", "emptyDir": {"medium": "Memory", "sizeLimit": "8Mi"}},
                                           {"name": "state", "emptyDir": {"sizeLimit": "64Mi"}},
                                           {"name": "temporary", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]):
            fail("credential_configuration_changed")

    def plan_rollouts(self, journal):
        values = _read(self.directory / "values.json")
        self.verify_consumers(values)
        result = {}
        for name in CONSUMERS:
            row = values["components"][name]
            namespace = row["namespace"]
            target, secret = self.get("deployment", name, namespace), self.get("secret", name + "-config", namespace)
            self.validate_target(name, row, values, target)
            if (target["spec"].get("replicas") != 1 or target["spec"].get("strategy") != {"type": "Recreate"}
                    or target["spec"]["template"]["metadata"]["annotations"].get(REVISION) != row["configRevision"]
                    or digest(secret["data"]) != row["configRevision"]):
                fail("credential_configuration_changed")
            changes = _changes(journal, name)
            planned = dict(secret["data"])
            for key, entry in changes.items():
                if key not in planned or base64.b64decode(planned[key], validate=True) != private_read(entry["old_file"], maximum=4096):
                    fail("credential_configuration_changed")
                planned[key] = base64.b64encode(private_read(entry["file"], maximum=4096)).decode()
            result[name] = {"namespace": namespace, "secret_uid": secret["metadata"]["uid"],
                            "deployment_uid": target["metadata"]["uid"], "template_sha256": self.template_hash(target),
                            "before": self.data_hashes(secret), "after": self.data_hashes({**secret, "data": planned}),
                            "old_revision": row["configRevision"], "new_revision": digest(planned),
                            "files": {key: entry["file"] for key, entry in changes.items()},
                            "started_at": None, "verified_at": None, "complete": False}
        return result

    def preflight_consumers(self, journal):
        values = _read(self.directory / "values.json")
        if self.assembly._desired_images(self.deployment, copy.deepcopy(values), self.owner):
            fail("credential_configuration_changed")
        self.verify_consumers(values)
        for name in CONSUMERS:
            row = values["components"][name]
            target, secret = self.get("deployment", name, row["namespace"]), self.get("secret", name + "-config", row["namespace"])
            self.validate_target(name, row, values, target)
            if (target["spec"]["template"]["metadata"]["annotations"].get(REVISION) != row["configRevision"]
                    or digest(secret["data"]) != row["configRevision"]):
                fail("credential_configuration_changed")
            for key, entry in _changes(journal, name).items():
                if base64.b64decode(secret["data"].get(key, ""), validate=True) != private_read(entry["old_file"], maximum=4096):
                    fail("credential_configuration_changed")

    def rollout(self, name, plan, save):
        namespace = plan["namespace"]
        target, secret = self.get("deployment", name, namespace), self.get("secret", name + "-config", namespace)
        if (target["metadata"]["uid"] != plan["deployment_uid"] or secret["metadata"]["uid"] != plan["secret_uid"]
                or self.template_hash(target) != plan["template_sha256"]
                or target["spec"].get("replicas") != 1 or target["spec"].get("strategy") != {"type": "Recreate"}
                or self.data_hashes(secret) not in (plan["before"], plan["after"])):
            fail("credential_configuration_changed")
        revision = target["spec"]["template"]["metadata"]["annotations"].get(REVISION)
        if revision not in (plan["old_revision"], plan["new_revision"]):
            fail("credential_configuration_changed")
        if plan["started_at"] is None:
            plan["started_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            save()
        if not plan["complete"]:
            self.assembly.stop_forwards(self.deployment, names={"runtime"} if name.startswith("runtime-") else {name.removeprefix("gateway-")})
        if self.data_hashes(secret) == plan["before"]:
            data = dict(secret["data"])
            for key, filename in plan["files"].items():
                data[key] = base64.b64encode(private_read(filename, maximum=4096)).decode()
            if self.data_hashes({**secret, "data": data}) != plan["after"] or digest(data) != plan["new_revision"]:
                fail("credential_configuration_changed")
            self.patch("secret", name + "-config", namespace, secret,
                       [{"op": "test", "path": "/data", "value": secret["data"]},
                        {"op": "replace", "path": "/data", "value": data}])
        if revision == plan["old_revision"]:
            self.patch("deployment", name, namespace, target,
                       [{"op": "test", "path": "/spec", "value": target["spec"]},
                        {"op": "replace", "path": "/spec/template/metadata/annotations/guardrails.evolution~1config-revision", "value": plan["new_revision"]}])
        self.assembly._ready(self.deployment, name, namespace, timeout=math.ceil(self.remaining(180)))
        # The existing private init copy must contain exactly the renewed bytes.
        expected = {key: plan["after"][key] for key in plan["files"]}
        script = "import hashlib,json,pathlib; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/config').iterdir() if p.name in " + repr(tuple(expected)) + "},sort_keys=True))"
        code, raw = self.deployment.kubectl("exec", "deployment/" + name, "-c", "runtime" if name.startswith("runtime-") else "gateway", "--", "python", "-B", "-c", script,
                                       namespace=namespace, timeout=self.remaining())
        if code or strict_json_loads(raw) != expected:
            fail("credential_private_copy_mismatch")
        current = self.get("deployment", name, namespace)
        current_secret = self.get("secret", name + "-config", namespace)
        if (current["metadata"]["uid"] != plan["deployment_uid"] or self.template_hash(current) != plan["template_sha256"]
                or current["spec"]["template"]["metadata"]["annotations"].get(REVISION) != plan["new_revision"]
                or current_secret["metadata"]["uid"] != plan["secret_uid"]
                or self.data_hashes(current_secret) != plan["after"] or digest(current_secret["data"]) != plan["new_revision"]):
            fail("credential_configuration_changed")
        if not plan["complete"]:
            plan["verified_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            plan["complete"] = True
        save()

    def receipt_proof(self, journal):
        deadline = min(self.deadline, time.monotonic() + 90)
        while True:
            if time.monotonic() >= deadline:
                fail("credential_runtime_receipt_missing")
            ready = True
            for tenant, value in self.inventory["tenants"].items():
                with self.db(), self.repository.transaction(tenant) as tx:
                    tx.lock("governance-admission")
                    expected = journal["bindings"][tenant]
                    state = self.binding_state(tx, expected["workload_id"])
                    if digest(state) != expected["sha256"]:
                        fail("credential_binding_changed")
                    for name, runtime in value["runtimes"].items():
                        install = tx.get("runtimes", runtime["runtime_id"], required=True)
                        seen = install.get("last_seen_at")
                        verified = journal["rollouts"][name]["verified_at"]
                        if verified is None or install["status"] != "ready" or seen is None or parse_time(seen) < parse_time(verified):
                            ready = False
                            continue
                        pointer = tx.get("snapshot_pointers", runtime["runtime_id"])
                        generation = pointer["generation"] if pointer else 0
                        receipt = tx.get("receipts", f"{runtime['runtime_id']}:{expected['workload_id']}:{generation}")
                        enrollment = tx.get("runtime_enrollments", runtime["runtime_id"])
                        if (receipt is None or enrollment is None or enrollment["principal_id"] != runtime["principal_id"]
                                or receipt["tenant_id"] != tenant or receipt["runtime_id"] != runtime["runtime_id"]
                                or receipt["workload_id"] != expected["workload_id"]
                                or receipt["binding_revision"] != state["binding"]["revision"]
                                or receipt["digest"] != state["binding"]["policy"]["digest"]
                                or receipt["health"] != "ready" or parse_time(receipt["applied_at"]) < parse_time(verified)
                                or not BindingService.target_ready(tx, runtime["runtime_id"], receipt)):
                            ready = False
            if ready:
                return
            if time.monotonic() >= deadline:
                fail("credential_runtime_receipt_missing")
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def verify_pending(self, journal):
        self.owner_unchanged()
        _validate_transition(journal, self.directory)
        for name, pair in journal["metadata"].items():
            if private_read(self.directory / name) != base64.b64decode(pair["after"], validate=True):
                fail("credential_metadata_changed")
        for name, plan in journal["rollouts"].items():
            target = self.get("deployment", name, plan["namespace"])
            secret = self.get("secret", name + "-config", plan["namespace"])
            if (not plan["complete"] or target["metadata"]["uid"] != plan["deployment_uid"]
                    or secret["metadata"]["uid"] != plan["secret_uid"]
                    or self.template_hash(target) != plan["template_sha256"]
                    or target["spec"].get("replicas") != 1 or target["spec"].get("strategy") != {"type": "Recreate"}
                    or target["spec"]["template"]["metadata"]["annotations"].get(REVISION) != plan["new_revision"]
                    or self.data_hashes(secret) != plan["after"] or digest(secret["data"]) != plan["new_revision"]):
                fail("credential_configuration_changed")
        self.verify_consumers(_read(self.directory / "values.json"))
        self.receipt_proof(journal)
        for value in self.access["tenants"].values():
            self.assembly._https_ready(value["gateway_url"] + "/health/ready", self.access["ca_file"],
                                       token=_token(value["gateway_token_file"], self.directory), timeout=self.remaining(30))

    def retire(self, journal, save):
        by_slot = {entry["slot"]: entry for entry in journal["entries"]}
        for entry in journal["entries"]:
            if entry["scope"] == "_platform" or entry["retired"]:
                continue
            admin = by_slot[entry["scope"] + "/tenant_admin"]
            path = f"/v2/tenants/{entry['scope']}/credentials/{entry['old_credential_id']}/revoke"
            self.request("POST", path, admin["file"], body={"reason": "Task credential renewal completed"}, key=entry["key"])
            entry["retired"] = True
            save()
        for entry in journal["entries"]:
            self.authenticate(entry["file"], entry)
            if entry["scope"] != "_platform":
                try:
                    self.authenticate(entry["old_file"], entry)
                except EvolutionError as exc:
                    if exc.code != "authentication_failed":
                        raise
                else:
                    fail("credential_retirement_failed")


def _cas(path, before, after):
    current = private_read(path)
    expected, planned = base64.b64decode(before, validate=True), base64.b64decode(after, validate=True)
    if current not in (expected, planned):
        fail("credential_metadata_changed")
    if current != planned:
        seed.private_write(path, planned, replace=True)


def renew(deployment, *, renewal_id, expires_at, recover_lost=False):
    """Explicit resumable maintenance. No automatic issuance during access/replay."""
    generation = _uuid(renewal_id)
    expiry = parse_time(expires_at)
    if not datetime.now(UTC) < expiry <= datetime.now(UTC) + timedelta(hours=24):
        fail("credential_expiry_invalid")
    expires_at = expiry.isoformat().replace("+00:00", "Z")
    with deployment.lock():
        assembly = deployment.support("assembly")
        directory, owner = assembly._state(deployment)
        relocation = assembly._relocation_receipt(directory, owner)
        if relocation and relocation[1]["status"] == "pending":
            fail("credential_relocation_pending")
        base = _directory(_base(directory))
        folder = _directory(base / generation)
        pending = base / "pending.json"
        path = folder / "journal.json"
        process = Renewal(deployment, directory, owner)
        try:
            process.owner_unchanged()
            if path.exists():
                _, journal = _journal(directory, generation, owner)
                if journal["expires_at"] != expires_at:
                    fail("credential_journal_mismatch")
            else:
                active = _active(directory, owner)
                journal = {"schema_version": "task-credential-renewal-v1", "generation": generation,
                           "owner_id": owner["owner_id"], "node_id": owner["container_id"],
                           "history": _history(directory), "expires_at": expires_at,
                           "previous": active["generation"] if active else None, "status": "issuing",
                           "entries": process.entries(active), "rollouts": {}, "metadata": {},
                           "bindings": process.capture_bindings(),
                           "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
                for entry in journal["entries"]:
                    entry["file"] = str(folder / (entry["slot"].replace("/", "-") + ".token"))
                _write(path, journal)
            if journal["status"] != "complete" and journal["node_id"] != owner["container_id"]:
                fail("credential_pending_node_changed")
            active_path = base / "active.json"
            if active_path.exists() or active_path.is_symlink():
                active_pointer = _read(active_path)
                if (type(active_pointer) is not dict or set(active_pointer) != {"generation", "sha256"}
                        or active_pointer["generation"] not in {journal["previous"], generation}):
                    fail("credential_overlay_changed")
                previous_path, previous_record = _journal(directory, active_pointer["generation"], owner)
                if previous_record["status"] != "complete" or digest(private_read(previous_path)) != active_pointer["sha256"]:
                    fail("credential_overlay_changed")
            elif journal["previous"] is not None:
                fail("credential_overlay_changed")
            expected_pending = {"generation": generation}
            if (pending.exists() or pending.is_symlink()) and _read(pending) != expected_pending:
                fail("credential_renewal_pending")
            if not pending.exists() and journal["status"] != "complete":
                _write(pending, expected_pending)
            def save():
                _write(path, journal)
            if journal["status"] == "issuing":
                process.preflight_consumers(journal)
                process.issue(journal, save, recover_lost=recover_lost)
                journal["rollouts"] = process.plan_rollouts(journal)
                values = _read(directory / "values.json")
                access = _read(directory / "access.json")
                for name, plan in journal["rollouts"].items():
                    values["components"][name]["configRevision"] = plan["new_revision"]
                for entry in journal["entries"]:
                    if entry["slot"].endswith("/workload"):
                        access["tenants"][entry["scope"]]["workload_token_file"] = entry["file"]
                journal["metadata"] = {name: {"before": base64.b64encode(private_read(directory / name)).decode(),
                                             "after": base64.b64encode(canonical_json_bytes(value)).decode()}
                                       for name, value in (("values.json", values), ("access.json", access))}
                journal["status"] = "rolling"
                _validate_transition(journal, directory)
                save()
            if journal["status"] == "rolling":
                for name, plan in journal["rollouts"].items():
                    process.rollout(name, plan, save)
                process.receipt_proof(journal)
                process.verify_consumers(strict_json_loads(base64.b64decode(journal["metadata"]["values.json"]["after"], validate=True)))
                ports = assembly._port_record(deployment, directory)
                for name in ("runtime", "team-a", "team-b"):
                    assembly._forward(deployment, directory, name, ports[name])
                for value in process.access["tenants"].values():
                    assembly._https_ready(value["gateway_url"] + "/health/ready", process.access["ca_file"],
                                          token=_token(value["gateway_token_file"], directory), timeout=30)
                for name, pair in journal["metadata"].items():
                    _cas(directory / name, pair["before"], pair["after"])
                journal["status"] = "retiring"
                save()
            if journal["status"] == "retiring":
                process.verify_pending(journal)
                process.retire(journal, save)
                journal["status"] = "complete"
                save()
            if pending.exists():
                process.verify_pending(journal)
            pointer = {"generation": generation, "sha256": digest(private_read(path))}
            active_path = base / "active.json"
            if active_path.exists():
                old = _read(active_path)
                if old["generation"] not in {journal["previous"], generation}:
                    fail("credential_overlay_changed")
            _write(active_path, pointer)
            if pending.exists():
                if _read(pending) != expected_pending:
                    fail("credential_renewal_pending")
                pending.unlink()
                fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return {"status": "renewed", "generation": generation, "credentials": 17, "consumers": 4,
                    "expires_at": expires_at, "historical_seed_preserved": _history(directory) == journal["history"]}
        finally:
            process.close()
