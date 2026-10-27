"""One real completed-platform replay after both bindings advance past Observe."""

from hashlib import sha256

from cluster_support import Cluster
from evolution.seed import private_read


def binding_projection(cluster, tenant):
    value = cluster.inventory["tenants"][tenant]
    workload = value["workload"]["workload_id"]
    details = cluster.management(tenant, "GET", "/workloads/" + workload)
    projection = {}
    with cluster.repo.transaction(tenant) as tx:
        for name in ("desired_binding", "applied_binding"):
            pointer = details[name]
            revision = pointer["binding_revision"]
            binding = tx.get("bindings", workload + ":" + str(revision), required=True)
            if name == "applied_binding":
                assert pointer["policy"] == binding["policy"]
            projection[name] = {
                "binding_revision": revision,
                "policy": binding["policy"],
            }
            if name == "desired_binding":
                projection["state"] = binding["state"]
                projection["release_id"] = binding["release_id"]
    return projection


def test_completed_platform_replay_preserves_advanced_bindings_inventory_and_zero_dispatch():
    cluster = Cluster("deterministic_mock")
    try:
        revisions = {
            tenant: cluster.release(tenant, "warn") for tenant in cluster.tenants
        }
        before = {
            tenant: binding_projection(cluster, tenant) for tenant in cluster.tenants
        }
        for tenant, projection in before.items():
            assert projection["state"] == "warn"
            assert (
                projection["desired_binding"]["binding_revision"] == revisions[tenant]
            )
            assert (
                projection["applied_binding"]["binding_revision"] == revisions[tenant]
            )
            assert revisions[tenant] > 1
        inventory_path = cluster.access["seed_inventory_file"]
        inventory_before = private_read(inventory_path)
        credentials_before = cluster._credentials()
        token_digests_before = {
            path: sha256(private_read(path, maximum=4096)).hexdigest()
            for path in credentials_before["token_files"]
        }
        calls_before = cluster.calls()["count"]

        # Always runs production full integrity verification. It must not rerun
        # initial Observe seeding or wait for obsolete initial binding receipts.
        cluster.platform()
        access_after = cluster._access()
        access_unchanged = access_after == cluster.access
        assert access_unchanged, "completed replay changed protected access identity"
        cluster.assert_ready()
        inventory_unchanged = private_read(inventory_path) == inventory_before
        assert inventory_unchanged, "completed replay rewrote immutable seed inventory"
        credentials_after = cluster._credentials()
        credentials_unchanged = credentials_after == credentials_before
        assert credentials_unchanged, "completed replay changed active credential generation or slots"
        tokens_unchanged = token_digests_before == {
            path: sha256(private_read(path, maximum=4096)).hexdigest()
            for path in credentials_after["token_files"]
        }
        assert tokens_unchanged, "completed replay changed historical or renewed secret bytes"
        after = {
            tenant: binding_projection(cluster, tenant) for tenant in cluster.tenants
        }
        assert after == before
        assert cluster.calls()["count"] == calls_before
        cluster.assert_private(())
    finally:
        cluster.close()
