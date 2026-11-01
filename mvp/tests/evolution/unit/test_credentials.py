"""Private renewal journals and CAS rollout ports; no cluster claims."""
import base64
import copy
import importlib.util
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("test_deployment_credentials", ROOT / "deploy/evolution/credentials.py")
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)


def private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials.seed.private_write(path, value, replace=path.exists())
    return str(path)


def token(path, scope, credential_id=None):
    cid = credential_id or str(uuid4())
    private(path, (f"gr2.{scope}.{cid}." + "s" * 43).encode())
    return cid


@pytest.fixture
def saved(tmp_path):
    directory = tmp_path / "assembly"
    directory.mkdir(mode=0o700)
    owner = {"owner_id": "fixture-owner", "container_id": "a" * 64}
    inventory = {"tenants": {}}
    for tenant in ("team-a", "team-b"):
        roles = {}
        for role in credentials.ROLES:
            path = directory / "seed" / f"{tenant}-{role}.token"
            roles[role] = {"principal_id": str(uuid4()), "credential_id": token(path, tenant),
                           "token_file": str(path), "expires_at": "2027-01-01T00:00:00Z"}
        inventory["tenants"][tenant] = {"roles": roles, "workload": {"workload_id": str(uuid4())},
            "runtimes": {name: {**roles[name], "runtime_id": str(uuid4())} for name in ("runtime-1", "runtime-2")},
            "gateway_token_file": private(directory / "seed" / f"{tenant}-gateway.token", b"fixture-ingress-" + tenant.encode()),
            "policy_ref": {"id": str(uuid4()), "version": 8, "digest": "b" * 64}}
    platform = directory / "seed/platform.token"
    token(platform, "_platform")
    private(directory / "seed/bootstrap.json", {"platform_principal_id": str(uuid4()), "platform_token_file": str(platform)})
    private(directory / "seed/inventory.json", inventory)
    private(directory / "seed-spec.json", {"immutable": "spec"})
    private(directory / "seed/operations.json", {"immutable": "operations"})
    generation = str(uuid4())
    folder = credentials._directory(credentials._directory(credentials._base(directory)) / generation)
    entries = []
    expires = (datetime.now(UTC) + timedelta(hours=20)).isoformat().replace("+00:00", "Z")
    for slot, row in credentials._historical_slots(directory).items():
        path = folder / (slot.replace("/", "-") + ".token")
        cid = token(path, row["scope"])
        entries.append({"slot": slot, **row, "key": str(uuid4()), "file": str(path),
                        "receipt": {"credential_id": cid, "expires_at": expires},
                        "token_sha256": credentials.digest(path.read_bytes()),
                        "old_sha256": credentials.digest(Path(row["old_file"]).read_bytes()),
                        "retired": row["scope"] != "_platform", "orphan": None})
    journal = {"schema_version": "task-credential-renewal-v1", "generation": generation,
        "owner_id": owner["owner_id"], "node_id": owner["container_id"],
        "history": credentials._history(directory), "expires_at": expires, "previous": None,
        "status": "complete", "entries": entries, "rollouts": {}, "metadata": {},
        "bindings": {tenant: {"workload_id": row["workload"]["workload_id"], "sha256": "d" * 64} for tenant, row in inventory["tenants"].items()},
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
    access = {"tenants": {tenant: {"workload_token_file": row["roles"]["workload"]["token_file"]} for tenant, row in inventory["tenants"].items()}}
    values = {"components": {}}
    for name in credentials.CONSUMERS:
        namespace = "gr-platform" if name.startswith("runtime-") else "gr-" + name.removeprefix("gateway-")
        changes = credentials._changes(journal, name)
        before_data = {key: base64.b64encode(Path(e["old_file"]).read_bytes()).decode() for key, e in changes.items()}
        before_data["tls.crt"] = base64.b64encode(b"retained-cert").decode()
        after_data = {**before_data, **{key: base64.b64encode(Path(e["file"]).read_bytes()).decode() for key, e in changes.items()}}
        before_revision, after_revision = credentials.digest(before_data), credentials.digest(after_data)
        values["components"][name] = {"namespace": namespace, "configRevision": before_revision}
        journal["rollouts"][name] = {"namespace": namespace, "secret_uid": str(uuid4()), "deployment_uid": str(uuid4()),
            "template_sha256": "c" * 64,
            "before": credentials.Renewal.data_hashes({"type": "Opaque", "data": before_data}),
            "after": credentials.Renewal.data_hashes({"type": "Opaque", "data": after_data}),
            "old_revision": before_revision, "new_revision": after_revision,
            "files": {k: e["file"] for k, e in changes.items()},
            "started_at": journal["created_at"], "verified_at": journal["created_at"], "complete": True}
    after_access, after_values = copy.deepcopy(access), copy.deepcopy(values)
    for tenant in inventory["tenants"]:
        after_access["tenants"][tenant]["workload_token_file"] = next(e["file"] for e in entries if e["slot"] == tenant + "/workload")
    for name, plan in journal["rollouts"].items():
        after_values["components"][name]["configRevision"] = plan["new_revision"]
    for name, before, after in (("access.json", access, after_access), ("values.json", values, after_values)):
        private(directory / name, after)
        journal["metadata"][name] = {"before": base64.b64encode(credentials.canonical_json_bytes(before)).decode(),
                                     "after": base64.b64encode(credentials.canonical_json_bytes(after)).decode()}
    path = folder / "journal.json"
    credentials._write(path, journal)
    credentials._write(credentials._base(directory) / "active.json", {"generation": generation, "sha256": credentials.digest(path.read_bytes())})
    return directory, owner, inventory, journal, path


def publish(saved):
    directory, _, _, journal, path = saved
    credentials._write(path, journal)
    credentials._write(credentials._base(directory) / "active.json", {"generation": journal["generation"], "sha256": credentials.digest(path.read_bytes())})


def test_overlay_rebinds_only_credentials_preserves_history_and_survives_cold_node(saved):
    directory, owner, inventory, journal, _ = saved
    before = copy.deepcopy(inventory)
    files = {p: p.read_bytes() for p in (directory / "seed").iterdir()}
    view = credentials.describe_inventory(directory, {**owner, "container_id": "d" * 64}, inventory)
    assert view["generation"] == journal["generation"]
    assert inventory == before
    assert len(view["token_files"]) == 36
    for tenant in inventory["tenants"]:
        assert view["inventory"]["tenants"][tenant]["policy_ref"] == inventory["tenants"][tenant]["policy_ref"]
        for role, old in inventory["tenants"][tenant]["roles"].items():
            current = view["inventory"]["tenants"][tenant]["roles"][role]
            assert current["principal_id"] == old["principal_id"]
            assert current["token_file"] != old["token_file"]
        for name, runtime in inventory["tenants"][tenant]["runtimes"].items():
            assert view["inventory"]["tenants"][tenant]["runtimes"][name]["runtime_id"] == runtime["runtime_id"]
            assert view["inventory"]["tenants"][tenant]["runtimes"][name]["token_file"] == view["inventory"]["tenants"][tenant]["roles"][name]["token_file"]
    assert all(p.read_bytes() == raw for p, raw in files.items())


@pytest.mark.parametrize("reader", ["resolve_inventory", "describe_inventory"])
def test_pending_blocks_direct_consumers_before_mixed_generation(saved, reader):
    directory, owner, inventory, journal, _ = saved
    credentials._write(credentials._base(directory) / "pending.json", {"generation": journal["generation"]})
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_renewal_pending$"):
        getattr(credentials, reader)(directory, owner, inventory)


@pytest.mark.parametrize("slot", ["platform", "team-a/tenant_admin", "team-b/author", "team-a/runtime-1", "team-b/workload"])
def test_same_id_changed_secret_bytes_refuse_overlay(saved, slot):
    directory, owner, inventory, journal, _ = saved
    entry = next(e for e in journal["entries"] if e["slot"] == slot)
    path = Path(entry["file"])
    private(path, path.read_bytes()[:-1] + b"x")
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_identity_changed$"):
        credentials.resolve_inventory(directory, owner, inventory)


@pytest.mark.parametrize("mutation", ["principal", "path", "scope", "policy", "metadata", "unknown"])
def test_journal_transition_tampering_refused_even_with_rehashed_pointer(saved, mutation):
    directory, owner, inventory, journal, _ = saved
    if mutation == "principal":
        journal["entries"][2]["principal_id"] = str(uuid4())
    elif mutation == "path":
        journal["entries"][2]["file"] = journal["entries"][3]["file"]
    elif mutation == "scope":
        journal["entries"][2]["scope"] = "team-b"
    elif mutation == "unknown":
        journal["unexpected"] = True
    else:
        pair = journal["metadata"]["values.json" if mutation == "policy" else "access.json"]
        value = json.loads(base64.b64decode(pair["after"]))
        value["unapproved-change"] = "not-a-credential"
        pair["after"] = base64.b64encode(credentials.canonical_json_bytes(value)).decode()
    publish(saved)
    with pytest.raises(credentials.CredentialRenewalError):
        credentials.describe_inventory(directory, owner, inventory)


def test_resolver_rejects_changed_noncredential_input(saved):
    directory, owner, inventory, _, _ = saved
    changed = copy.deepcopy(inventory)
    changed["tenants"]["team-a"]["policy_ref"]["version"] += 1
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_inventory_changed$"):
        credentials.resolve_inventory(directory, owner, changed)


@pytest.mark.parametrize("point", ["before_link", "after_link"])
def test_first_journal_publication_is_atomic_and_collision_never_overwrites(tmp_path, monkeypatch, point):
    path = tmp_path / "journal.json"
    real = os.link
    def crash(source, target, **kwargs):
        if point == "after_link":
            real(source, target, **kwargs)
        raise InterruptedError()
    with monkeypatch.context() as patch:
        patch.setattr(credentials.os, "link", crash)
        with pytest.raises(InterruptedError):
            credentials._write(path, {"complete": True})
    assert not list(tmp_path.glob("*.staged"))
    if point == "before_link":
        assert not path.exists()
    else:
        assert credentials._read(path) == {"complete": True}
        assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_first_publication_refuses_racing_foreign_record(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    real = os.link
    def race(source, target, **kwargs):
        private(path, b"foreign-record")
        return real(source, target, **kwargs)
    monkeypatch.setattr(credentials.os, "link", race)
    with pytest.raises(FileExistsError):
        credentials._write(path, {"ours": True})
    assert path.read_bytes() == b"foreign-record"


@pytest.mark.parametrize("unsafe", ["symlink", "mode", "outside"])
def test_overlay_rejects_unsafe_private_token_paths(saved, tmp_path, unsafe):
    directory, owner, inventory, journal, _ = saved
    path = Path(journal["entries"][0]["file"])
    if unsafe == "symlink":
        raw = path.read_bytes()
        path.unlink()
        target = tmp_path / "outside"
        private(target, raw)
        path.symlink_to(target)
    elif unsafe == "mode":
        path.chmod(0o644)
    else:
        journal["entries"][0]["file"] = str(tmp_path / "outside")
        publish(saved)
    with pytest.raises((credentials.CredentialRenewalError, credentials.EvolutionError, OSError)):
        credentials.resolve_inventory(directory, owner, inventory)


class CasPort:
    def __init__(self, saved):
        self.directory, self.identity, _, self.journal, _ = saved
        self.events = []
        self.fail = None
        self.race = None
        self.rows = {}
        for name, plan in self.journal["rollouts"].items():
            changes = credentials._changes(self.journal, name)
            data = {key: base64.b64encode(Path(e["old_file"]).read_bytes()).decode() for key, e in changes.items()}
            data["tls.crt"] = base64.b64encode(b"retained-cert").decode()
            meta = {"name": name, "namespace": plan["namespace"], "uid": plan["deployment_uid"], "resourceVersion": "1", "labels": {credentials.OWNER: self.identity["owner_id"]}}
            target = {"metadata": meta, "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "template": {"metadata": {"annotations": {credentials.REVISION: plan["old_revision"]}}, "spec": {"unchanged": True}}}}
            self.rows[("deployment", name)] = target
            self.rows[("secret", name + "-config")] = {"metadata": {**meta, "name": name + "-config", "uid": plan["secret_uid"]}, "type": "Opaque", "data": data}
            plan["template_sha256"] = credentials.Renewal.template_hash(target)
            plan["complete"] = False
            plan["started_at"] = None
            plan["verified_at"] = None
    def owner(self):
        return self.identity
    def run(self, argv, **kwargs):
        assert argv == ["docker", "container", "inspect", "guardrails-evolution"]
        return 0, json.dumps([{"Id": self.identity["container_id"], "Name": "/guardrails-evolution", "State": {"Running": True}}])
    def kubectl(self, action, kind, name=None, *args, **kwargs):
        if action == "exec":
            component = kind.split("/")[1]
            data = self.rows[("secret", component + "-config")]["data"]
            return 0, json.dumps({key: credentials.digest(base64.b64decode(data[key])) for key in self.journal["rollouts"][component]["files"]})
        key = kind, name
        if action == "get":
            return 0, json.dumps(self.rows[key])
        assert action == "patch"
        operations = json.loads(kwargs["input_text"])
        row = self.rows[key]
        if self.race:
            row["metadata"]["resourceVersion"] = "raced"
        # Execute actual JSON Pointer test/replace semantics.
        for operation in operations:
            segments = [p.replace("~1", "/").replace("~0", "~") for p in operation["path"].split("/")[1:]]
            parent = row
            for segment in segments[:-1]:
                parent = parent[segment]
            if operation["op"] == "test":
                if parent[segments[-1]] != operation["value"]:
                    raise RuntimeError("fixture_cas_rejected")
            else:
                assert operation["op"] == "replace"
                parent[segments[-1]] = operation["value"]
        row["metadata"]["resourceVersion"] = str(int(row["metadata"]["resourceVersion"]) + 1)
        self.events.append(kind)
        if self.fail == kind:
            self.fail = None
            raise InterruptedError()
        return 0, "{}"


def process_for(port):
    process = object.__new__(credentials.Renewal)
    process.deployment, process.owner, process.directory = port, port.identity, port.directory
    process.deadline = time.monotonic() + 30
    process.assembly = SimpleNamespace(stop_forwards=lambda *a, **kw: port.events.append("stop"), _ready=lambda *a, **kw: port.events.append("ready"))
    return process


@pytest.mark.parametrize("point", ["secret", "deployment"])
def test_rollout_recovers_after_each_cas_without_second_secret_or_extra_restart(saved, point):
    port = CasPort(saved)
    process = process_for(port)
    name = "runtime-1"
    plan = port.journal["rollouts"][name]
    before_template = copy.deepcopy(port.rows[("deployment", name)]["spec"]["template"]["spec"])
    port.fail = point
    with pytest.raises(InterruptedError):
        process.rollout(name, plan, lambda: None)
    process.rollout(name, plan, lambda: None)
    assert plan["complete"]
    assert port.events.count("secret") == 1
    assert port.events.count("deployment") == 1
    assert port.rows[("deployment", name)]["spec"]["template"]["spec"] == before_template
    assert credentials.Renewal.data_hashes(port.rows[("secret", name + "-config")]) == plan["after"]
    mutations = list(port.events)
    process.rollout(name, plan, lambda: None)
    assert port.events == mutations + ["ready"]


@pytest.mark.parametrize("tamper", ["foreign_owner", "deployment_uid", "secret_uid", "template", "tls_bytes", "revision", "cas_race"])
def test_rollout_refuses_foreign_or_changed_configuration_before_token_write(saved, tamper):
    port = CasPort(saved)
    process = process_for(port)
    name = "runtime-1"
    plan = port.journal["rollouts"][name]
    target, secret = port.rows[("deployment", name)], port.rows[("secret", name + "-config")]
    if tamper == "foreign_owner":
        secret["metadata"]["labels"] = {credentials.OWNER: "foreign"}
    elif tamper == "deployment_uid":
        target["metadata"]["uid"] = "foreign"
    elif tamper == "secret_uid":
        secret["metadata"]["uid"] = "foreign"
    elif tamper == "template":
        target["spec"]["template"]["spec"]["changed"] = True
    elif tamper == "tls_bytes":
        secret["data"]["tls.crt"] = base64.b64encode(b"foreign").decode()
    elif tamper == "revision":
        target["spec"]["template"]["metadata"]["annotations"][credentials.REVISION] = "foreign"
    else:
        port.race = True
    before = copy.deepcopy(secret["data"])
    with pytest.raises((credentials.CredentialRenewalError, RuntimeError)):
        process.rollout(name, plan, lambda: None)
    assert secret["data"] == before
    assert "deployment" not in port.events

@pytest.mark.parametrize("condition", ["ready", "old_heartbeat", "wrong_digest", "wrong_generation", "foreign_principal", "expired_lease"])
def test_runtime_proof_requires_post_copy_current_exact_receipt(saved, condition):
    from contextlib import nullcontext
    directory, _, inventory, journal, _ = saved
    process = object.__new__(credentials.Renewal)
    process.directory, process.inventory = directory, inventory
    now = datetime.now(UTC)
    def stamp(delta):
        return (now + timedelta(seconds=delta)).isoformat().replace("+00:00", "Z")
    records = {}
    for tenant, value in inventory["tenants"].items():
        workload = value["workload"]["workload_id"]
        policy = {"id": str(uuid4()), "version": 3, "digest": "e" * 64}
        state = {"desired": {"binding_revision": 9}, "applied": {"binding_revision": 9, "policy": policy}, "binding": {"revision": 9, "policy": policy}}
        journal["bindings"][tenant]["sha256"] = credentials.digest(state)
        rows = {("binding_pointers", workload): state["desired"], ("applied_bindings", workload): state["applied"], ("bindings", workload + ":9"): state["binding"]}
        for name, runtime in value["runtimes"].items():
            rid, pid = runtime["runtime_id"], runtime["principal_id"]
            journal["rollouts"][name]["started_at"] = stamp(-10)
            journal["rollouts"][name]["verified_at"] = stamp(-2)
            rows.update({("runtimes", rid): {"status": "ready", "last_seen_at": stamp(-5 if condition == "old_heartbeat" else -1)},
                ("snapshot_pointers", rid): {"generation": 7},
                ("runtime_enrollments", rid): {"tenant_id": tenant, "principal_id": "foreign" if condition == "foreign_principal" else pid},
                ("runtime_principals", pid): {"tenant_id": tenant, "runtime_id": rid},
                ("principals", pid): {"principal_id": pid, "status": "active", "roles": ["runtime"]},
                ("issued_snapshots", rid + ":7"): {"expires_at": stamp(-1 if condition == "expired_lease" else 30)},
                ("receipts", f"{rid}:{workload}:7"): {"tenant_id": tenant, "runtime_id": rid, "workload_id": workload,
                    "binding_revision": 9, "digest": "f" * 64 if condition == "wrong_digest" else policy["digest"],
                    "health": "ready", "applied_at": stamp(-1), "snapshot_generation": 6 if condition == "wrong_generation" else 7}})
        records[tenant] = rows
    class Transaction:
        def __init__(self, tenant):
            self.tenant_id = tenant
        def lock(self, key):
            assert key == "governance-admission"
        def get(self, kind, key, **kwargs):
            return records[self.tenant_id].get((kind, key))
    process.repository = SimpleNamespace(transaction=lambda tenant: nullcontext(Transaction(tenant)))
    process.deadline = time.monotonic() + 0.02
    if condition == "ready":
        process.receipt_proof(journal)
        assert all(records[t][("runtimes", r["runtime_id"])]["status"] == "ready" for t, v in inventory["tenants"].items() for r in v["runtimes"].values())
    else:
        with pytest.raises(credentials.CredentialRenewalError, match="^credential_runtime_receipt_missing$"):
            process.receipt_proof(journal)


def test_optional_seed_plan_dangling_symlink_is_not_treated_as_absent(saved):
    directory, owner, inventory, _, _ = saved
    (directory / "seed/bootstrap-plan.json").symlink_to(directory / "not-created")
    with pytest.raises(OSError):
        credentials.resolve_inventory(directory, owner, inventory)

@pytest.mark.parametrize("change", ["active_digest", "old_workload_pointer"])
def test_completed_access_guard_rejects_corrupt_active_state(saved, change):
    directory, owner, inventory, _, _ = saved
    if change == "active_digest":
        pointer = credentials._read(credentials._base(directory) / "active.json")
        pointer["sha256"] = "0" * 64
        credentials._write(credentials._base(directory) / "active.json", pointer)
    else:
        access = credentials._read(directory / "access.json")
        access["tenants"]["team-a"]["workload_token_file"] = inventory["tenants"]["team-a"]["roles"]["workload"]["token_file"]
        credentials._write(directory / "access.json", access)
    with pytest.raises(credentials.CredentialRenewalError):
        credentials.ensure_available(directory, owner)


@pytest.mark.parametrize("target", ["journal", "token"])
def test_fifo_private_input_is_rejected_without_open_blocking(saved, target):
    import subprocess
    import sys
    directory, owner, _, journal, path = saved
    path = path if target == "journal" else Path(journal["entries"][0]["file"])
    path.unlink()
    os.mkfifo(path, 0o600)
    code = """import importlib.util,json,pathlib,sys
s=importlib.util.spec_from_file_location('renewal_fifo',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
try: m.ensure_available(pathlib.Path(sys.argv[2]),json.loads(sys.argv[3]))
except m.CredentialRenewalError: print('private_file_rejected')
else: raise RuntimeError('unexpected_acceptance')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "deploy/evolution/credentials.py"), str(directory), json.dumps(owner)], capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "private_file_rejected"
    assert not result.stderr


@pytest.fixture
def rendered_target(tmp_path):
    import subprocess

    specification = importlib.util.spec_from_file_location("renewal_assembly_render", ROOT / "deploy/evolution/assembly.py")
    assembly = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(assembly)
    helm = Path(os.environ.get("EVOLUTION_DEPLOYMENT_STATE", ROOT / ".evolution/deployment")) / "bin/helm"
    row = assembly._component("gr-platform", "runtime", "fixture@sha256:" + "a" * 64, "b" * 64, command=["python", "-B", "-m", "runtime"])
    values = {"ownerId": "fixture-owner", "initImage": "fixture@sha256:" + "c" * 64, "servicesOnly": False, "components": {"runtime-1": row}}
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    result = subprocess.run([str(helm), "template", "renewal", str(ROOT / "deploy/evolution/charts/platform"), "-f", str(path)], capture_output=True, text=True, timeout=10, check=True)
    # Reuse the already pinned Helm YAML decoder, without adding a wheel.
    decoder = tmp_path / "decode"
    (decoder / "templates").mkdir(parents=True)
    (decoder / "Chart.yaml").write_text("apiVersion: v2\nname: manifest-decode\nversion: 1.0.0\n")
    (decoder / "manifest.yaml").write_text(result.stdout)
    (decoder / "templates/catalog.json").write_text(
        '{{- $documents := list -}}'
        '{{- range splitList "\\n---\\n" (.Files.Get "manifest.yaml") -}}'
        '{{- if trim . -}}{{- $documents = append $documents (fromYaml .) -}}{{- end -}}'
        '{{- end -}}'
        '{{ dict "apiVersion" "v1" "kind" "List" "items" $documents | toJson }}')
    decoded = subprocess.run([str(helm), "template", "decode", str(decoder)], capture_output=True, text=True, timeout=10, check=True)
    raw = "\n".join(line for line in decoded.stdout.splitlines() if line.strip() != "---" and not line.startswith("#"))
    target, = [value for value in json.loads(raw)["items"] if value.get("kind") == "Deployment"]
    process = object.__new__(credentials.Renewal)
    process.deployment, process.assembly = SimpleNamespace(root=ROOT), assembly
    return process, row, values, target


@pytest.mark.parametrize("tamper", [None, "initializer_code", "initializer_mount", "initializer_limits", "initializer_environment", "writable_config", "extra_volume", "host_network"])
def test_rendered_private_initializer_contract_refuses_credential_exposure(rendered_target, tamper):
    process, row, values, target = rendered_target
    spec = target["spec"]["template"]["spec"]
    init, app = spec["initContainers"][0], spec["containers"][0]
    if tamper == "initializer_code":
        init["args"] = ["print('foreign initializer')"]
    elif tamper == "initializer_mount":
        init["volumeMounts"][0]["readOnly"] = False
    elif tamper == "initializer_limits":
        init["resources"]["limits"]["cpu"] = "2"
    elif tamper == "initializer_environment":
        init["env"] = [{"name": "PYTHONPATH", "value": "/foreign"}]
    elif tamper == "writable_config":
        app["volumeMounts"][0]["readOnly"] = False
    elif tamper == "extra_volume":
        spec["volumes"].append({"name": "extra", "hostPath": {"path": "/tmp"}})
    elif tamper == "host_network":
        spec["hostNetwork"] = True
    if tamper is None:
        process.validate_target("runtime-1", row, values, target)
        assert init["image"] == values["initImage"]
    else:
        with pytest.raises(credentials.CredentialRenewalError, match="^credential_configuration_changed$"):
            process.validate_target("runtime-1", row, values, target)


def test_actual_deployment_support_loader_exposes_finite_renewal_command(tmp_path, monkeypatch):
    specification = importlib.util.spec_from_file_location("renewal_bootstrap_loader", ROOT / "deploy/evolution/bootstrap.py")
    bootstrap = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bootstrap)
    deployment = bootstrap.LocalDeployment(ROOT, state=tmp_path)
    module = deployment.support("credentials")
    assert callable(module.renew) and callable(module.describe_inventory)
    # Actual wrapper resolves the production loader again; invalid ID fails before any port.
    monkeypatch.setattr(deployment, "run", lambda *a, **k: pytest.fail("invalid request reached a command"))
    with pytest.raises(bootstrap.DeploymentError) as caught:
        deployment.renew_credentials(renewal_id="private-bad-value", expires_at="also-private")
    assert str(caught.value) == "credential_renewal_invalid"
    assert "private-bad-value" not in str(caught.value)


@pytest.mark.parametrize("condition", ["absent", "stopped", "wrong_name", "wrong_id"])
def test_stale_owner_marker_cannot_authorize_renewal(saved, condition):
    port = CasPort(saved)
    process = process_for(port)
    row = {"Id": port.identity["container_id"], "Name": "/guardrails-evolution", "State": {"Running": True}}
    if condition == "stopped":
        row["State"]["Running"] = False
    elif condition == "wrong_name":
        row["Name"] = "/foreign"
    elif condition == "wrong_id":
        row["Id"] = "f" * 64
    port.run = lambda *a, **k: (1, "") if condition == "absent" else (0, json.dumps([row]))
    with pytest.raises(credentials.CredentialRenewalError, match="^credential_node_not_running$"):
        process.owner_unchanged()
    assert not port.events
