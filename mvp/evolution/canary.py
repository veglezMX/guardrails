"""Observe applied runtime caches and HTTPS receipts; sign scoped canary evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import ssl
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from guardrails.evolution import models
from guardrails.evolution.cli import _secret
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.http_client import BoundedJsonClient
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore

from .dependencies import (
    ROOT,
    canonical,
    digest,
    verify_artifacts,
    verify_environment,
    verify_installed_wheels,
    verify_runtime_artifacts,
)
from .registry import EvidenceError
from .run import (
    lock_files,
    assert_same_source,
    sign_report,
    signing_key,
    source_files,
    write_once,
)


def stamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def checked(condition, code):
    if not condition:
        raise EvidenceError(code)


def observe(args, *, root=ROOT):
    origin = urlsplit(args.url)
    checked(
        origin.scheme == "https"
        and origin.hostname
        and not origin.username
        and not origin.password
        and not origin.query
        and not origin.fragment
        and origin.path in ("", "/"),
        "https_origin_required",
    )
    checked(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.tenant), "invalid_tenant")
    release_id = str(UUID(args.release_id))
    checked(0 < args.timeout <= 60, "invalid_deadline")
    targets = models.strict_json_loads(
        Path(args.targets_file).read_bytes(), max_bytes=1024 * 1024
    )
    checked(isinstance(targets, dict) and 1 <= len(targets) <= 100, "invalid_targets")
    for workload, runtime_ids in targets.items():
        checked(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", workload)
            and isinstance(runtime_ids, list)
            and 1 <= len(runtime_ids) <= 100
            and len(set(runtime_ids)) == len(runtime_ids),
            "invalid_targets",
        )
        checked(
            all(str(UUID(value)) == value for value in runtime_ids), "invalid_targets"
        )
    expected_ids = {value for group in targets.values() for value in group}
    caches = {}
    for value in args.runtime_cache:
        runtime_id, separator, path = value.partition("=")
        checked(
            separator
            and runtime_id not in caches
            and str(UUID(runtime_id)) == runtime_id,
            "invalid_runtime_cache",
        )
        caches[runtime_id] = Path(path)
    checked(set(caches) == expected_ids, "runtime_inventory_mismatch")
    public = Ed25519PublicKey.from_public_bytes(
        Path(args.snapshot_key_file).read_bytes()
    )
    key, key_id = signing_key(root)
    token = _secret(args.credential_file)
    source = source_files(root)
    locks = lock_files(source)
    verify_environment(root / "evolution-requirements.lock")
    artifacts = verify_artifacts(root) + verify_runtime_artifacts(root)
    verify_installed_wheels(root)
    started_at, monotonic_start = stamp(), time.monotonic()
    deadline = monotonic_start + args.timeout
    run_id = str(uuid4())
    destination = root / ".evolution/reports" / run_id
    destination.mkdir(parents=True, mode=0o700)
    headers = {"Authorization": "Bearer " + token, "X-Request-ID": str(uuid4())}
    base = f"/v2/tenants/{args.tenant}"
    try:
        with BoundedJsonClient(
            base_url=args.url.rstrip("/"),
            verify=ssl.create_default_context(cafile=args.ca_file),
            deadline=deadline,
        ) as client:

            def request(method, path, body=None):
                remaining = deadline - time.monotonic()
                checked(remaining > 0, "canary_deadline")
                request_headers = dict(headers)
                if method != "GET":
                    request_headers["Idempotency-Key"] = run_id
                response = client.request(
                    method, path, headers=request_headers, json=body, timeout=remaining
                )
                checked(
                    response.status_code == (201 if method == "POST" else 200),
                    "canary_management_request_failed",
                )
                return models.strict_json_loads(
                    response.content, max_bytes=8 * 1024 * 1024
                )

            state = request("GET", base + "/releases/" + release_id)
            release = models.Release.model_validate_json(canonical(state["release"]))
            checked(
                release.tenant_id == args.tenant
                and release.stage == "canary"
                and release.status == "applied"
                and set(release.workload_ids) == set(targets),
                "canary_not_applied",
            )
            remote = [
                models.RuntimeReceipt.model_validate_json(canonical(value)).model_dump(
                    mode="json"
                )
                for value in state["receipts"]
            ]
            observations, coverage = [], {workload: set() for workload in targets}
            attribution_count, health_count = 0, 0
            timings = {
                name: 0.0
                for name in (
                    "canary-policy-attribution",
                    "canary-runtime-health",
                    "canary-target-coverage",
                )
            }
            for runtime_id, path in caches.items():
                store = RuntimeSnapshotStore(
                    path, args.tenant, runtime_id, {args.snapshot_key_id: public}
                )
                checked(store.ready(), "canary_snapshot_stale")
                snapshot = store.snapshot
                for local in store.receipts:
                    workload = local["workload_id"]
                    if workload not in targets or runtime_id not in targets[workload]:
                        continue
                    check_started = time.monotonic()
                    checked(
                        local in remote
                        and local["binding_revision"]
                        == release.expected_revisions[workload] + 1
                        and local["digest"] == release.policy.digest
                        and store.policy_for(workload).compiled_digest
                        == release.policy.digest,
                        "canary_policy_mismatch",
                    )
                    attribution_count += 4
                    timings["canary-policy-attribution"] += (
                        time.monotonic() - check_started
                    ) * 1000
                    check_started = time.monotonic()
                    checked(
                        local["health"] == "ready"
                        and models.parse_time(snapshot.issued_at)
                        <= models.parse_time(local["applied_at"])
                        <= datetime.now(UTC)
                        and models.parse_time(snapshot.expires_at) > datetime.now(UTC),
                        "canary_runtime_unhealthy",
                    )
                    health_count += 3
                    timings["canary-runtime-health"] += (
                        time.monotonic() - check_started
                    ) * 1000
                    coverage[workload].add(runtime_id)
                    observations.append(
                        {
                            "receipt": local,
                            "snapshot_digest": snapshot.digest,
                            "snapshot_issued_at": snapshot.issued_at,
                            "snapshot_expires_at": snapshot.expires_at,
                        }
                    )
            check_started = time.monotonic()
            checked(
                all(
                    coverage[workload] == set(ids) for workload, ids in targets.items()
                ),
                "canary_target_missing",
            )
            timings["canary-target-coverage"] = (
                time.monotonic() - check_started
            ) * 1000
            observed_path = destination / "observations.json"
            write_once(
                observed_path,
                {
                    "tenant_id": args.tenant,
                    "release_id": release_id,
                    "targets": targets,
                    "observations": observations,
                },
            )
            artifact = {
                "name": "canary-runtime-observations",
                "version": "1",
                "source_url": observed_path.as_uri(),
                "sha256": digest(observed_path),
                "size_bytes": observed_path.stat().st_size,
                "license": "generated-test-data",
                "kind": "fixture",
            }
            manifest = {
                "source_files": source,
                "lock_files": locks,
                "observation_sha256": artifact["sha256"],
            }
            write_once(destination / "manifest.json", manifest)
            counts = {
                "canary-policy-attribution": attribution_count,
                "canary-runtime-health": health_count,
                "canary-target-coverage": len(targets) + 1,
            }
            report = {
                "run_id": run_id,
                "created_at": started_at,
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip(),
                "source_digest": hashlib.sha256(canonical(source)).hexdigest(),
                "lock_digest": hashlib.sha256(canonical(locks)).hexdigest(),
                "profile": "canary",
                "phase": 1,
                "started_at": started_at,
                "finished_at": stamp(),
                "cases": [
                    {
                        "case_id": name,
                        "requirement_id": "P1.7",
                        "status": "pass",
                        "duration_ms": timings[name],
                        "assertions": count,
                        "reason_codes": [],
                    }
                    for name, count in sorted(counts.items())
                ],
                "artifact_digests": artifacts + [artifact],
                "status": "pass",
                "environment": {
                    "tenant_id": args.tenant,
                    "release_id": release_id,
                    "policy_digest": release.policy.digest,
                    "python": platform.python_version(),
                    "manifest_sha256": digest(destination / "manifest.json"),
                },
            }
            assert_same_source(source, root)
            verify_environment(root / "evolution-requirements.lock")
            checked(
                artifacts == verify_artifacts(root) + verify_runtime_artifacts(root),
                "artifacts_changed_during_observation",
            )
            verify_installed_wheels(root)
            report = sign_report(report, key, key_id)
            models.EvidenceReport.model_validate_json(canonical(report))
            write_once(destination / "report.json", report)
            if args.submit:
                receipt = request("POST", base + "/evidence", report)
                checked(
                    receipt == {"run_id": run_id, "status": "pass"},
                    "canary_evidence_not_accepted",
                )
                write_once(destination / "submission.json", receipt)
            return {
                "status": "pass",
                "run_id": run_id,
                "report": str(destination / "report.json"),
                "submitted": args.submit,
            }
    except (
        EvidenceError,
        EvolutionError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        httpx.HTTPError,
    ):
        write_once(
            destination / "diagnostic.json",
            {"run_id": run_id, "status": "fail", "reason": "canary_observation_failed"},
        )
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "url",
        "ca-file",
        "credential-file",
        "tenant",
        "release-id",
        "targets-file",
        "snapshot-key-file",
        "snapshot-key-id",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--runtime-cache",
        action="append",
        required=True,
        help="Runtime UUID=protected cache path, once per target",
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--submit", action="store_true")
    try:
        result = observe(parser.parse_args(argv))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        EvidenceError,
        EvolutionError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        httpx.HTTPError,
    ):
        print(json.dumps({"status": "fail", "reason": "canary_observation_failed"}))
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
