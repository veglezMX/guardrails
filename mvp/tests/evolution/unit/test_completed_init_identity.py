"""Completed-init identity uses retained OCI/CRI/rootfs evidence, never aliases."""

import copy

import pytest
from test_image_identity import ALIAS, CHAIN, CID, ContentStore, identity

STARTED = 1791628200000000000
FINISHED = STARTED + 1_000_000_000


class CompletedInit(ContentStore):
    """Explicit read-only runtime metadata port; no cluster process is started."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status.update(
            name="caddy-capless",
            ready=False,
            state={
                "terminated": {
                    "exitCode": 0,
                    "reason": "Completed",
                    "containerID": "containerd://" + CID,
                    "startedAt": "2026-10-10T11:50:00Z",
                    "finishedAt": "2026-10-10T11:50:01Z",
                }
            },
        )
        self.live["status"].update(
            state="CONTAINER_EXITED",
            exitCode=0,
            metadata={"name": "caddy-capless", "attempt": 0},
            startedAt=str(STARTED),
            finishedAt=str(FINISHED),
        )
        self.missing = None
        self.final_change = None

    def run(self, argv, *, timeout, check):
        args = argv[3:]
        names = {
            "cri": ["crictl", "inspect", "--output=json", CID],
            "image": ["crictl", "inspecti", "--output=json", ALIAS],
            "container": ["ctr", "-n=k8s.io", "containers", "info", CID],
            "snapshot": [
                "ctr",
                "-n=k8s.io",
                "snapshots",
                "--snapshotter",
                "overlayfs",
                "info",
                CID,
            ],
        }
        if self.missing is not None and args == names[self.missing]:
            self.calls.append((list(argv), timeout, check))
            return 1, "private collected-container diagnostics"
        if args == names["cri"] and self.live_reads == 1 and self.final_change:
            self.live["status"].update(self.final_change)
        return super().run(argv, timeout=timeout, check=check)


@pytest.mark.parametrize("index", [False, True], ids=["manifest", "index"])
def test_completed_init_requires_exact_oci_config_and_retained_rootfs(index):
    store = CompletedInit(index=index)
    result = identity.verify_completed_init(store, store.expected, store.status)
    assert result == {
        "container_id": CID,
        "expected_reference": store.expected,
        "manifest_digest": store.manifest["digest"],
        "config_digest": store.config["digest"],
        "rootfs_chain_id": CHAIN,
        "completed": True,
        "exit_code": 0,
        "finished_at_ns": FINISHED,
    }
    assert store.status["imageID"] != store.expected
    assert store.container["Image"] != store.expected
    assert store.live_reads == 2
    assert len(store.calls) == (8 if index else 7)
    assert all(
        0 < timeout <= identity.VERIFY_TIMEOUT and check is False
        for _, timeout, check in store.calls
    )


@pytest.mark.parametrize(
    "fault",
    [
        "running",
        "exit",
        "boolean_exit",
        "container_id",
        "missing_time",
        "naive_time",
        "reversed_time",
        "missing_name",
    ],
)
def test_invalid_kubernetes_completion_is_rejected_before_runtime_io(fault):
    store = CompletedInit()
    terminated = store.status["state"]["terminated"]
    if fault == "running":
        store.status["state"] = {"running": {"startedAt": terminated["startedAt"]}}
    elif fault == "exit":
        terminated["exitCode"] = 1
    elif fault == "boolean_exit":
        terminated["exitCode"] = False
    elif fault == "container_id":
        terminated["containerID"] = "containerd://" + "d" * 64
    elif fault == "missing_time":
        del terminated["finishedAt"]
    elif fault == "naive_time":
        terminated["finishedAt"] = "2026-10-10T11:50:01"
    elif fault == "reversed_time":
        terminated["finishedAt"] = "2026-10-10T11:49:59Z"
    else:
        del store.status["name"]
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_init_not_completed$"
    ):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "CONTAINER_RUNNING"),
        ("exitCode", 1),
        ("exitCode", False),
        ("id", "f" * 64),
        ("metadata", {"name": "foreign-init"}),
        ("startedAt", "0"),
        ("finishedAt", str(STARTED - 1)),
    ],
    ids=[
        "running",
        "failed_exit",
        "boolean_exit",
        "foreign_id",
        "foreign_name",
        "zero_start",
        "reversed_time",
    ],
)
def test_claimed_success_cannot_override_actual_cri_exit_or_identity(field, value):
    store = CompletedInit()
    store.live["status"][field] = value
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_init_not_completed$"
    ):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.live_reads == 1
    assert not any("inspecti" in args for args, _, _ in store.calls)


@pytest.mark.parametrize("missing", ["cri", "image", "container", "snapshot"])
def test_collected_completion_evidence_never_falls_back_to_status_image(missing):
    store = CompletedInit()
    store.status["imageID"] = store.expected
    store.missing = missing
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_unavailable$"
    ) as failure:
        identity.verify_completed_init(store, store.expected, store.status)
    assert str(failure.value) == "image_identity_unavailable"
    assert store.live_reads < 2


@pytest.mark.parametrize("fault", ["config_bytes", "actual_config", "snapshot_parent"])
def test_completed_alias_does_not_bypass_pinned_bytes_or_actual_rootfs(fault):
    store = CompletedInit(index=True)
    if fault == "config_bytes":
        store.blobs[store.config["digest"]] += "\n"
        code = "image_identity_blob_mismatch"
    elif fault == "actual_config":
        store.image["status"]["id"] = "sha256:" + "f" * 64
        code = "image_identity_config_mismatch"
    else:
        store.snapshot["Parent"] = "sha256:" + "f" * 64
        code = "image_identity_snapshot_mismatch"
    assert store.expected in store.image["status"]["repoDigests"]
    with pytest.raises(identity.ImageIdentityError, match="^" + code + "$"):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.live_reads < 2


@pytest.mark.parametrize(
    "change,code",
    [
        ({"finishedAt": str(FINISHED + 1)}, "image_identity_container_changed"),
        ({"imageRef": "sha256:" + "e" * 64}, "image_identity_container_changed"),
        ({"exitCode": 1}, "image_identity_init_not_completed"),
    ],
    ids=["finish_changed", "image_ref_changed", "exit_changed"],
)
def test_completion_is_rechecked_after_rootfs_verification(change, code):
    store = CompletedInit()
    store.final_change = change
    with pytest.raises(identity.ImageIdentityError, match="^" + code + "$"):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.live_reads == 2


def test_completed_entry_point_does_not_weaken_running_container_contract():
    store = CompletedInit()
    ready_status = copy.deepcopy(store.status)
    ready_status["ready"] = True
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_container_not_running$"
    ):
        identity.verify_container(store, store.expected, ready_status)
    assert store.live_reads == 1
    assert not any("inspecti" in args for args, _, _ in store.calls)


def test_completed_verification_timeout_is_finite_without_driver_output():
    store = CompletedInit()
    store.timeout = True
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_timeout$"
    ) as failure:
        identity.verify_completed_init(store, store.expected, store.status)
    assert str(failure.value) == "image_identity_timeout"
    assert len(store.calls) == 1


@pytest.mark.parametrize("offset", ["Z", "+00:00"], ids=["utc_z", "utc_offset"])
def test_actual_crictl_rfc3339_nanoseconds_preserve_completion_identity(offset):
    store = CompletedInit()
    store.live["status"].update(
        startedAt="2026-10-10T15:58:51.173116799" + offset,
        finishedAt="2026-10-10T15:59:17.122786862" + offset,
    )
    terminated = store.status["state"]["terminated"]
    terminated.update(
        startedAt="2026-10-10T15:58:51Z", finishedAt="2026-10-10T15:59:17Z"
    )
    # A successfully completed init is ready=true in actual Kubernetes status.
    store.status["ready"] = True
    result = identity.verify_completed_init(store, store.expected, store.status)
    assert result["finished_at_ns"] == 1791647957122786862
    assert result["completed"] is True and result["exit_code"] == 0
    assert result["config_digest"] == store.config["digest"]
    assert result["rootfs_chain_id"] == CHAIN and store.live_reads == 2


def test_crictl_nanosecond_change_is_detected_without_microsecond_rounding():
    store = CompletedInit()
    store.live["status"].update(
        startedAt="2026-10-10T15:58:51.173116799Z",
        finishedAt="2026-10-10T15:59:17.122786862Z",
    )
    store.final_change = {"finishedAt": "2026-10-10T15:59:17.122786863Z"}
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_container_changed$"
    ):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.live_reads == 2


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-10T15:59:17.122786862",  # timezone absent
        "2026-10-10T15:59:17.1227868629Z",  # excess precision
        "2026-02-30T15:59:17Z",  # impossible date
        "2026-10-10T15:59:17+24:00",  # invalid UTC offset
        "2026-10-10T15:59:17.1Z private",  # trailing arbitrary text
        "1969-12-31T23:59:59.999999999Z",  # before epoch
        "2262-04-11T23:47:16.854775808Z",  # int64 overflow
    ],
    ids=["naive", "precision", "date", "offset", "suffix", "negative", "overflow"],
)
def test_invalid_crictl_timestamp_never_reaches_image_or_rootfs_verification(timestamp):
    store = CompletedInit()
    store.live["status"]["finishedAt"] = timestamp
    with pytest.raises(
        identity.ImageIdentityError, match="^image_identity_init_not_completed$"
    ):
        identity.verify_completed_init(store, store.expected, store.status)
    assert store.live_reads == 1
    assert not any("inspecti" in argv for argv, _, _ in store.calls)
