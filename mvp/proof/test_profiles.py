"""Closed, staged gate inventories. Missing and skipped cases fail a gate."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "unit": {
        "foundation": ["mvp/tests/unit/test_proof_contract.py", "mvp/tests/proof/unit/test_runtime_client.py"],
        "management": ["mvp/tests/unit/test_policy_integrity.py"],
        "runtime": ["mvp/tests/unit/test_pii_formats.py", "mvp/tests/unit/test_failure_precedence.py", "mvp/tests/unit/test_snapshot_cache.py", "mvp/tests/unit/test_turn_context.py", "mvp/tests/unit/test_deadline_executor.py"],
    },
    "contract": {
        "foundation": ["mvp/tests/contract/test_proof_transport.py", "mvp/tests/proof/unit/test_gateway_boundary.py"],
        "management": ["mvp/tests/contract/test_api_validation.py", "mvp/tests/contract/test_authorization.py"],
        "runtime": ["mvp/tests/contract/test_runtime_contract.py"],
    },
    "integration": {
        "smoke": ["mvp/tests/integration/test_proof_smoke.py"],
        "management": ["mvp/tests/integration/test_management_lifecycle.py", "mvp/tests/integration/test_sqlite_restart.py"],
        "runtime": ["mvp/tests/integration/test_snapshot_distribution.py", "mvp/tests/integration/test_runtime_faults.py"],
    },
}
ALL_EXTRAS = {
    "unit": [],
    "contract": ["mvp/tests/contract/test_cli_api_parity.py", "mvp/tests/contract/test_gateway_surface.py"],
    "integration": ["mvp/tests/integration/test_litellm_gateways.py", "mvp/tests/integration/test_offline_stack.py"],
}


class GatePlugin:
    def __init__(self, required_files):
        self.required_files = set(required_files)
        self.collected = set()
        self.skipped = False

    def pytest_collection_modifyitems(self, session, config, items):
        self.collected = {str(Path(item.path).resolve().relative_to(ROOT)) for item in items}
        missing = self.required_files - self.collected
        if missing:
            import pytest
            raise pytest.UsageError("Required gate files collected no tests: " + ", ".join(sorted(missing)))

    def pytest_runtest_logreport(self, report):
        if report.skipped or hasattr(report, "wasxfail"):
            self.skipped = True

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped = True

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped or self.required_files - self.collected:
            session.exitstatus = 1


def selected_files(suite: str, profile: str) -> list[str]:
    if suite not in PROFILES:
        raise ValueError("Unknown suite")
    if profile == "all":
        return list(dict.fromkeys(p for group in PROFILES[suite].values() for p in group)) + ALL_EXTRAS[suite]
    if profile not in PROFILES[suite]:
        raise ValueError("Unknown profile")
    return PROFILES[suite][profile]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=PROFILES)
    parser.add_argument("profile", nargs="?", default="all")
    args = parser.parse_args()
    try:
        files = selected_files(args.suite, args.profile)
    except ValueError as error:
        parser.error(str(error))
    missing = [name for name in files if not (ROOT / name).is_file()]
    if missing:
        print("Gate is not implemented; required files missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    import pytest
    return int(pytest.main(["-q", "-p", "no:cacheprovider", *files], plugins=[GatePlugin(files)]))


if __name__ == "__main__":
    raise SystemExit(main())
