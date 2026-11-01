"""Compare-and-swap desired bindings; only real runtime receipts mark applied."""

from uuid import uuid4

from .errors import EvolutionError
from .governance import lock_admission, require_role
from .models import Binding, Release, RuntimeReceipt, parse_time, utc_now

STATES = ("disabled", "observe", "warn", "redact", "block")


class BindingService:
    def __init__(self, repository, governance, audit, *, capability_check=None):
        self.repository, self.governance, self.audit = repository, governance, audit
        self.capability_check = capability_check

    @staticmethod
    def latest_receipts(tx, workload_id, revision):
        latest, after = {}, None
        while True:
            batch = tx.page(
                "receipts",
                limit=100,
                after=after,
                filters={"workload_id": workload_id, "binding_revision": revision},
            )
            for row in batch:
                item = row["data"]
                if (
                    item["runtime_id"] not in latest
                    or item["snapshot_generation"]
                    > latest[item["runtime_id"]]["snapshot_generation"]
                ):
                    latest[item["runtime_id"]] = item
            if len(batch) < 100:
                return latest
            after = (batch[-1]["created_at"], batch[-1]["key"])

    def binding_ready(self, tx, binding):
        workload_id = binding["workload_id"]
        desired = tx.get("binding_pointers", workload_id)
        if desired is None or desired["binding_revision"] != binding["revision"]:
            return False
        targets = (tx.get("runtime_targets", workload_id) or {}).get("runtime_ids", [])
        latest = self.latest_receipts(tx, workload_id, binding["revision"])
        return bool(targets) and all(
            target in latest
            and latest[target]["digest"] == binding["policy"]["digest"]
            and latest[target]["health"] == "ready"
            and self.target_ready(tx, target, latest[target])
            for target in targets
        )

    @staticmethod
    def target_ready(tx, runtime_id, receipt):
        install = tx.get("runtimes", runtime_id)
        if install is None or install["status"] != "ready":
            return False
        enrollment = tx.get("runtime_enrollments", runtime_id)
        if enrollment is None or enrollment.get("tenant_id") != tx.tenant_id:
            return False
        actor = tx.get("principals", enrollment["principal_id"])
        if (
            actor is None
            or actor["status"] != "active"
            or "runtime" not in actor["roles"]
        ):
            return False
        association = tx.get("runtime_principals", actor["principal_id"])
        if (
            association is None
            or association.get("runtime_id") != runtime_id
            or association.get("tenant_id") != tx.tenant_id
        ):
            return False
        pointer = tx.get("snapshot_pointers", runtime_id)
        if pointer is None or pointer["generation"] != receipt["snapshot_generation"]:
            return False
        issued = tx.get(
            "issued_snapshots", f"{runtime_id}:{receipt['snapshot_generation']}"
        )
        return issued is not None and parse_time(issued["expires_at"]) > parse_time(
            utc_now()
        )

    def _record_enforced(self, tx, binding):
        """Retain real application proof across later rollback/recovery attempts."""
        workload_id = binding["workload_id"]
        existing = tx.get("enforcement_history", workload_id)
        if existing is not None:
            return True
        if STATES.index(binding["state"]) <= STATES.index("observe"):
            return False
        targets = set(
            (tx.get("runtime_targets", workload_id) or {}).get("runtime_ids", [])
        )
        if not targets:
            return False
        receipts, after = {}, None
        while True:
            batch = tx.page(
                "receipts",
                limit=100,
                after=after,
                filters={
                    "workload_id": workload_id,
                    "binding_revision": binding["revision"],
                    "health": "ready",
                    "digest": binding["policy"]["digest"],
                },
            )
            for row in batch:
                receipt = row["data"]
                if (
                    receipt["runtime_id"] not in targets
                    or receipt["runtime_id"] in receipts
                ):
                    continue
                issued = tx.get(
                    "issued_snapshots",
                    f"{receipt['runtime_id']}:{receipt['snapshot_generation']}",
                )
                if (
                    issued is None
                    or not issued.get("issued_at")
                    or not issued.get("expires_at")
                    or not parse_time(issued["issued_at"])
                    <= parse_time(receipt["applied_at"])
                    < parse_time(issued["expires_at"])
                ):
                    continue
                if any(
                    item["workload_id"] == workload_id
                    and item["revision"] == binding["revision"]
                    and item["policy"] == binding["policy"]
                    for item in issued["bindings"]
                ):
                    receipts[receipt["runtime_id"]] = row["key"]
            if targets <= receipts.keys():
                break
            if len(batch) < 100:
                return False
            after = (batch[-1]["created_at"], batch[-1]["key"])
        tx.insert(
            "enforcement_history",
            workload_id,
            {
                "tenant_id": tx.tenant_id,
                "workload_id": workload_id,
                "binding_revision": binding["revision"],
                "policy": binding["policy"],
                "receipt_keys": sorted(receipts.values()),
            },
            immutable=True,
        )
        tx.link(
            "enforcement_history",
            workload_id,
            "bindings",
            f"{workload_id}:{binding['revision']}",
        )
        tx.link("enforcement_history", workload_id, "workloads", workload_id)
        for key in receipts.values():
            tx.link("enforcement_history", workload_id, "receipts", key)
        return True

    def _onboarded(self, tx, workload_id, *, require_observe):
        history = tx.get("enforcement_history", workload_id)
        if history is not None:
            binding = tx.get(
                "bindings",
                f"{workload_id}:{history['binding_revision']}",
                required=True,
            )
            if binding["policy"] != history["policy"] or STATES.index(
                binding["state"]
            ) <= STATES.index("observe"):
                raise EvolutionError("policy_unresolved")
            return
        applied = tx.get("applied_bindings", workload_id)
        binding = (
            tx.get("bindings", f"{workload_id}:{applied['binding_revision']}")
            if applied
            else None
        )
        # Compatibility for workloads applied before this private proof was
        # introduced. Reconstruct from genuine historical issued receipts.
        if (
            binding
            and applied["policy"] == binding["policy"]
            and STATES.index(binding["state"]) > STATES.index("observe")
            and self._record_enforced(tx, binding)
        ):
            return
        if not require_observe:
            return
        if (
            binding is None
            or binding["state"] != "observe"
            or applied["policy"] != binding["policy"]
        ):
            raise EvolutionError("floor_conflict")
        self.governance.eligible(tx, binding["policy"])
        if not self.binding_ready(tx, binding):
            raise EvolutionError("snapshot_stale")

    def release(
        self,
        tx,
        identity,
        *,
        policy,
        workload_ids,
        stage,
        state,
        expected_revisions,
        reason,
        canary_evidence_id=None,
    ):
        actor = require_role(identity, {"operator"}, tx.tenant_id)
        lock_admission(tx)
        # Validate the command before checking references or acquiring side effects.
        command = Release.model_validate(
            {
                "release_id": str(uuid4()),
                "tenant_id": tx.tenant_id,
                "policy": policy,
                "workload_ids": workload_ids,
                "stage": stage,
                "state": state,
                "expected_revisions": expected_revisions,
                "actor_id": actor["principal_id"],
                "reason": reason,
            }
        )
        policy = command.policy.model_dump(mode="json")
        workload_ids = list(command.workload_ids)
        expected_revisions = dict(command.expected_revisions)
        if (
            stage not in ("canary", "full")
            or state not in STATES
            or not 1 <= len(workload_ids) <= 100
            or len(set(workload_ids)) != len(workload_ids)
        ):
            raise EvolutionError("validation_failed")
        if (
            set(expected_revisions) != set(workload_ids)
            or not isinstance(reason, str)
            or not 1 <= len(reason) <= 500
        ):
            raise EvolutionError("validation_failed")
        compiled = self.governance.eligible(tx, policy)
        if stage == "full":
            evidence = tx.get("canary_evidence", str(canary_evidence_id), required=True)
            canary = tx.get("releases", evidence["release_id"], required=True)
            if (
                evidence.get("status") != "pass"
                or canary["stage"] != "canary"
                or canary["status"] != "applied"
                or canary["policy"] != policy
                or canary["state"] != state
            ):
                raise EvolutionError("approval_required")
            for workload_id in canary["workload_ids"]:
                revision = canary["expected_revisions"][workload_id] + 1
                binding = tx.get("bindings", f"{workload_id}:{revision}", required=True)
                applied = tx.get("applied_bindings", workload_id)
                if (
                    applied is None
                    or applied["binding_revision"] != revision
                    or not self.binding_ready(tx, binding)
                ):
                    raise EvolutionError("snapshot_stale")
        release_id = str(uuid4())
        bindings = []
        for workload_id in sorted(workload_ids):
            tx.lock("workload:" + workload_id)
            workload = tx.get("workloads", workload_id, required=True)
            if workload["status"] != "active":
                raise EvolutionError("forbidden")
            minimum = max(
                [STATES.index(workload["required_state"])]
                + [
                    STATES.index(rule["minimum_state"])
                    for rule in compiled["rules"]
                    if rule["mandatory"]
                ]
            )
            if STATES.index(state) < minimum:
                raise EvolutionError("floor_conflict")
            if (
                expected_revisions[workload_id] == 0
                and minimum <= STATES.index("observe")
                and state != "observe"
            ):
                raise EvolutionError("floor_conflict")
            self._onboarded(
                tx,
                workload_id,
                require_observe=(
                    minimum <= STATES.index("observe")
                    and STATES.index(state) > STATES.index("observe")
                ),
            )
            if any(
                not set(rule["stages"]).issubset(workload["allowed_stages"])
                for rule in compiled["rules"]
            ):
                raise EvolutionError("capability_missing")
            if not set(workload["provider_ids"]).issubset(
                {p["provider_id"] for p in compiled["providers"] if p["enabled"]}
            ):
                raise EvolutionError("provider_denied")
            targets = tx.get("runtime_targets", workload_id)
            for runtime_id in (targets or {}).get("runtime_ids", []):
                install = tx.get("runtimes", runtime_id, required=True)
                if install["status"] == "revoked":
                    raise EvolutionError("forbidden")
                if install["zone"] not in compiled["residency"]["allowed_zones"]:
                    raise EvolutionError("residency_denied")
                if install["mode"] != workload["mode"]:
                    raise EvolutionError("capability_missing")
            if self.capability_check:
                self.capability_check(compiled, workload)
            expected = expected_revisions[workload_id]
            binding = {
                "tenant_id": tx.tenant_id,
                "workload_id": workload_id,
                "revision": expected + 1,
                "policy": policy,
                "state": state,
                "release_id": release_id,
                "activated_by": actor["principal_id"],
                "previous_revision": expected or None,
                "effective_after": utc_now(),
                "reason": reason,
                "status": "pending",
                "created_at": utc_now(),
            }
            binding = Binding.model_validate(binding).model_dump(mode="json")
            tx.cas_binding(workload_id, expected, binding)
            blocked = tx.get("binding_blocks", workload_id)
            if blocked:
                tx.put(
                    "binding_blocks",
                    workload_id,
                    {**blocked, "binding_revision": expected + 1, "blocked": False},
                    expected_revision=blocked["revision"],
                )
            bindings.append(binding)
        release = {
            "release_id": release_id,
            "tenant_id": tx.tenant_id,
            "policy": policy,
            "workload_ids": workload_ids,
            "stage": stage,
            "state": state,
            "expected_revisions": expected_revisions,
            "status": "pending",
            "actor_id": actor["principal_id"],
            "reason": reason,
            "revision": 1,
            "created_at": utc_now(),
        }
        tx.insert("releases", release_id, release)
        tx.append(
            "outbox",
            {
                "type": "binding_desired",
                "release_id": release_id,
                "created_at": utc_now(),
            },
        )
        self.audit.append(
            tx,
            "release_pending",
            actor_id=actor["principal_id"],
            policy_digest=policy["digest"],
        )
        return release

    def rollback(
        self, tx, identity, workload_id, *, target_revision, expected_revision, reason
    ):
        require_role(identity, {"operator"}, tx.tenant_id)
        lock_admission(tx)
        if (
            type(target_revision) is not int
            or target_revision < 1
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise EvolutionError("validation_failed")
        historical = tx.get(
            "bindings", f"{workload_id}:{target_revision}", required=True
        )
        self.release(
            tx,
            identity,
            policy=historical["policy"],
            workload_ids=[workload_id],
            stage="canary",
            state=historical["state"],
            expected_revisions={workload_id: expected_revision},
            reason=reason,
        )
        return tx.get(
            "bindings", f"{workload_id}:{expected_revision + 1}", required=True
        )

    def receipt(self, tx, identity, receipt):
        actor = require_role(identity, {"runtime"}, tx.tenant_id)
        lock_admission(tx)
        receipt = RuntimeReceipt.model_validate(receipt).model_dump(mode="json")
        if receipt["tenant_id"] != tx.tenant_id:
            raise EvolutionError("resource_not_found")
        runtime_id = receipt["runtime_id"]
        install = tx.get("runtimes", runtime_id, required=True)
        association = tx.get("runtime_principals", actor["principal_id"])
        if (
            association is None
            or association.get("runtime_id") != runtime_id
            or association.get("tenant_id") != tx.tenant_id
            or install["status"] == "revoked"
        ):
            raise EvolutionError("forbidden")
        workload_id = receipt["workload_id"]
        targets = tx.get("runtime_targets", workload_id, required=True)["runtime_ids"]
        if runtime_id not in targets:
            raise EvolutionError("forbidden")
        tx.lock("workload:" + workload_id)
        binding = tx.get(
            "bindings", f"{workload_id}:{receipt['binding_revision']}", required=True
        )
        if binding["policy"]["digest"] != receipt["digest"]:
            raise EvolutionError("revision_conflict")
        self.governance.eligible(tx, binding["policy"])
        issued = tx.get(
            "issued_snapshots",
            f"{runtime_id}:{receipt['snapshot_generation']}",
            required=True,
        )
        if "expires_at" not in issued or parse_time(issued["expires_at"]) <= parse_time(
            utc_now()
        ):
            raise EvolutionError("snapshot_stale")
        if not any(
            b["workload_id"] == workload_id
            and b["revision"] == binding["revision"]
            and b["policy"]["digest"] == receipt["digest"]
            for b in issued["bindings"]
        ):
            raise EvolutionError("revision_conflict")
        key = f"{runtime_id}:{workload_id}:{receipt['snapshot_generation']}"
        existing = tx.get("receipts", key)
        comparable = lambda item: {
            key: value for key, value in item.items() if key != "created_at"
        }
        if existing and comparable(existing) != comparable(receipt):
            raise EvolutionError("revision_conflict")
        if existing:
            return existing
        if not existing:
            tx.insert("receipts", key, receipt, immutable=True)
        tx.put(
            "runtimes",
            runtime_id,
            {
                **install,
                "status": "ready" if receipt["health"] == "ready" else "stale",
                "last_seen_at": receipt["applied_at"],
            },
            expected_revision=install["revision"],
        )
        release = tx.get(
            "releases", binding["release_id"], required=True, for_update=True
        )
        latest = self.latest_receipts(tx, workload_id, binding["revision"])
        ready = self.binding_ready(tx, binding)
        if (
            any(
                target in latest and latest[target]["health"] == "failed"
                for target in targets
            )
            and release["status"] != "failed"
        ):
            tx.put(
                "releases",
                release["release_id"],
                {**release, "status": "failed"},
                expected_revision=release["revision"],
            )
            self.audit.append(
                tx,
                "release_failed",
                actor_id=actor["principal_id"],
                policy_digest=receipt["digest"],
            )
            return receipt
        if ready:
            previous = tx.get("applied_bindings", workload_id)
            if previous and previous["binding_revision"] > binding["revision"]:
                return receipt
            if previous is None or previous["binding_revision"] < binding["revision"]:
                data = {
                    "binding_revision": binding["revision"],
                    "policy": binding["policy"],
                    "revision": (previous["revision"] + 1) if previous else 1,
                    "created_at": utc_now(),
                }
                if previous:
                    tx.put(
                        "applied_bindings",
                        workload_id,
                        data,
                        expected_revision=previous["revision"],
                    )
                else:
                    tx.insert("applied_bindings", workload_id, data)
            self._record_enforced(tx, binding)
            all_applied = all(
                (tx.get("applied_bindings", w) or {}).get("binding_revision")
                == release["expected_revisions"][w] + 1
                and self.binding_ready(
                    tx,
                    tx.get(
                        "bindings",
                        f"{w}:{release['expected_revisions'][w] + 1}",
                        required=True,
                    ),
                )
                for w in release["workload_ids"]
            )
            if all_applied and release["status"] != "applied":
                tx.put(
                    "releases",
                    release["release_id"],
                    {
                        **release,
                        "status": "applied",
                        "revision": release["revision"] + 1,
                    },
                    expected_revision=release["revision"],
                )
                self.audit.append(
                    tx,
                    "release_applied",
                    actor_id=actor["principal_id"],
                    policy_digest=receipt["digest"],
                )
        return receipt
