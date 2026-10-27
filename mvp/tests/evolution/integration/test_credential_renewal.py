"""Renewal issuance/recovery over real PostgreSQL and verified HTTPS management."""
import copy
import importlib.util
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from evolution import seed
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("integration_credentials", ROOT / "deploy/evolution/credentials.py")
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)


class Interrupted(BaseException):
    pass


@pytest.fixture
def issued(tmp_path):
    dsn = Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip()
    repository = TenantRepository(dsn)
    identity = IdentityService(repository)
    tenant = "renew-" + uuid4().hex[:12]
    old_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    new_expiry = (datetime.now(UTC) + timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    folder = tmp_path / "tokens"
    folder.mkdir(mode=0o700)
    entries = []
    principals = {}
    workload = str(uuid4())
    for scope in ("_platform", tenant):
        with repository.transaction(scope) as tx:
            if tx.get("tenants", scope) is None:
                tx.insert("tenants", scope, {"tenant_id": scope, "status": "active"})
            if scope == tenant:
                tx.insert("workloads", workload, {"workload_id": workload, "tenant_id": tenant, "status": "active"})
            roles = ("platform_admin",) if scope == "_platform" else ("tenant_admin", "workload", "runtime")
            for role in roles:
                principal = identity.provision_principal(tx, "Renewal fixture", [role])
                result = identity.issue_credential(tx, principal["principal_id"], expires_at=old_expiry, workload_id=workload if role == "workload" else None)
                old_file = folder / (scope + "-" + role + "-old.token")
                seed.private_write(old_file, result["one_time_secret"].encode())
                slot = "platform" if scope == "_platform" else scope + "/" + role
                entry = {"slot": slot, "scope": scope, "principal_id": principal["principal_id"], "workload_id": workload if role == "workload" else None,
                         "old_credential_id": result["credential_id"], "old_file": str(old_file), "roles": [role], "key": str(uuid4()),
                         "file": str(folder / (slot.replace("/", "-") + ".token")), "receipt": None, "retired": False,
                         "token_sha256": None, "old_sha256": credentials.digest(old_file.read_bytes()), "orphan": None}
                entries.append(entry)
                principals[slot] = principal
    def factory():
        return create_app(service=ManagementApi(TenantRepository(dsn)))
    with serve_https(factory, tmp_path / "tls") as server:
        process = object.__new__(credentials.Renewal)
        process.repository, process.identity, process.directory = repository, identity, tmp_path
        process.access = {"management_url": server["url"], "ca_file": server["ca_file"]}
        process.deadline, process.last_request = time.monotonic() + 60, 0
        journal = {"entries": entries, "expires_at": new_expiry}
        saved = []
        yield process, journal, lambda: saved.append(copy.deepcopy(journal)), tenant, principals, saved
    repository.close()


def counts(process, journal):
    result = {}
    for entry in journal["entries"]:
        with process.repository.transaction(entry["scope"]) as tx:
            rows = tx.page("credentials", filters={"principal_id": entry["principal_id"]})
            result[entry["slot"]] = [r["data"] for r in rows]
    return result


def test_actual_https_renewal_preserves_principal_scope_and_retires_only_old_tenant_credentials(issued):
    process, journal, save, tenant, principals, saved = issued
    original = {entry["old_file"]: Path(entry["old_file"]).read_bytes() for entry in journal["entries"]}
    process.issue(journal, save)
    first = counts(process, journal)
    process.issue(journal, save)
    assert counts(process, journal) == first
    assert all(len(rows) == 2 for rows in first.values())
    for entry in journal["entries"]:
        current = process.authenticate(entry["file"], entry)
        previous = process.authenticate(entry["old_file"], entry)
        assert current.principal_id == previous.principal_id == principals[entry["slot"]]["principal_id"]
        assert current.credential["expires_at"] == journal["expires_at"]
        assert current.credential["expires_at"] > previous.credential["expires_at"]
        assert current.workload_id == entry["workload_id"]
        assert current.roles == tuple(entry["roles"])
    process.retire(journal, save)
    assert all(entry["retired"] for entry in journal["entries"] if entry["scope"] == tenant)
    assert journal["entries"][0]["retired"] is False
    assert process.authenticate(journal["entries"][0]["old_file"]).scope == "_platform"
    assert all(Path(path).read_bytes() == raw for path, raw in original.items())
    assert all(row["revoked_at"] is None for row in counts(process, journal)["platform"])
    assert all(row["token_hash"] != Path(e["file"]).read_text() for e in journal["entries"] for row in first[e["slot"]])
    assert saved and all("one_time_secret" not in credentials.canonical_json_bytes(value).decode() for value in saved)


@pytest.mark.parametrize("point", ["token_saved", "receipt_saved"])
def test_actual_issuance_recovers_commit_before_or_after_journal_save(issued, monkeypatch, point):
    process, journal, save, _, _, _ = issued
    real = credentials.seed.private_write
    initial = copy.deepcopy(journal)
    def write(path, value, **kwargs):
        real(path, value, **kwargs)
        if point == "token_saved" and str(path) == journal["entries"][0]["file"] and value:
            raise Interrupted()
    def interrupted_save():
        save()
        raise Interrupted()
    with monkeypatch.context() as patch:
        patch.setattr(credentials.seed, "private_write", write)
        with pytest.raises(Interrupted):
            process.issue(journal, interrupted_save if point == "receipt_saved" else save)
    if point == "token_saved":
        journal = initial
    process.issue(journal, save)
    assert all(len(rows) == 2 for rows in counts(process, journal).values())
    assert all(entry["receipt"] is not None for entry in journal["entries"])
    assert process.authenticate(journal["entries"][0]["file"]).principal_id == journal["entries"][0]["principal_id"]


@pytest.mark.parametrize("slot_kind", ["platform", "tenant_admin"])
def test_lost_secret_requires_explicit_single_replacement_and_retires_tenant_orphan(issued, monkeypatch, slot_kind):
    process, journal, save, _, _, _ = issued
    entry = next(e for e in journal["entries"] if e["slot"] == "platform" or e["slot"].endswith("/" + slot_kind)) if slot_kind == "platform" else next(e for e in journal["entries"] if e["slot"].endswith("/tenant_admin"))
    real = credentials.seed.private_write
    target = entry["file"]
    def lost(path, value, **kwargs):
        if str(path) == target and value:
            raise Interrupted()
        return real(path, value, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(credentials.seed, "private_write", lost)
        with pytest.raises(Interrupted):
            process.issue(journal, save)
    before = counts(process, journal)
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_secret_lost$"):
        process.issue(journal, save)
    assert counts(process, journal) == before
    process.issue(journal, save, recover_lost=True)
    orphan = entry["orphan"]
    assert orphan is not None and orphan["credential_id"] != entry["receipt"]["credential_id"]
    assert len(counts(process, journal)[entry["slot"]]) == 3
    with process.repository.transaction(entry["scope"]) as tx:
        row = tx.get("credentials", orphan["credential_id"])
    assert (row["revoked_at"] is not None) == (slot_kind != "platform")
    assert orphan["revoked"] == (slot_kind != "platform")
    assert process.authenticate(entry["old_file"], entry).principal_id == entry["principal_id"]
    assert process.authenticate(entry["file"], entry).principal_id == entry["principal_id"]


def test_second_lost_response_never_issues_third_replacement(issued, monkeypatch):
    process, journal, save, _, _, _ = issued
    real = credentials.seed.private_write
    platform = journal["entries"][0]
    def lost(path, value, **kwargs):
        if Path(path).name.startswith("platform") and value:
            raise Interrupted()
        return real(path, value, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(credentials.seed, "private_write", lost)
        with pytest.raises(Interrupted):
            process.issue(journal, save)
        with pytest.raises(Interrupted):
            process.issue(journal, save, recover_lost=True)
    before = counts(process, journal)
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_secret_lost$"):
        process.issue(journal, save, recover_lost=True)
    assert counts(process, journal) == before
    assert len(before["platform"]) == 3
    assert platform["receipt"] is None and platform["orphan"] is not None
    assert all(len(rows) == 1 for key, rows in before.items() if key != "platform")


def test_durable_new_platform_token_recovers_after_original_authority_expires(issued, monkeypatch):
    process, journal, save, _, _, _ = issued
    real = credentials.seed.private_write
    first = journal["entries"][0]
    def crash(path, value, **kwargs):
        real(path, value, **kwargs)
        if str(path) == first["file"] and value:
            raise Interrupted()
    with monkeypatch.context() as patch:
        patch.setattr(credentials.seed, "private_write", crash)
        with pytest.raises(Interrupted):
            process.issue(journal, save)
    with process.repository.transaction("_platform") as tx:
        old = tx.get("credentials", first["old_credential_id"])
        tx.put("credentials", old["credential_id"], {**old, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")}, expected_revision=old["revision"])
    with pytest.raises(credentials.EvolutionError) as failure:
        process.authenticate(first["old_file"])
    assert failure.value.code == "authentication_failed"
    process.issue(journal, save)
    assert all(len(rows) == 2 for rows in counts(process, journal).values())
    assert first["receipt"] is not None
    assert process.authenticate(first["file"]).principal_id == first["principal_id"]
