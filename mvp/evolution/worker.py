"""Isolated pytest worker: retain counts and static codes, never captured traffic."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakKeyDictionary

import pytest
import pytest_asyncio.plugin

from .diagnostics import SourceCatalog, capture, read_catalog, sidecar, write_diagnostic


class CasePlugin:
    def __init__(self, nodeid: str, source_catalog: dict):
        self.nodeid = nodeid
        self.assertions = 0
        self.collected = []
        self.phases = Counter()
        self.reasons = set()
        self.duration_ms = 0.0
        self.in_call = False
        self.failures = []
        self.catalog = SourceCatalog(Path.cwd(), source_catalog)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if report.failed and call.excinfo is not None and item.nodeid == self.nodeid:
            self.failures.append(capture(call.excinfo.value, call.when, self.catalog))

    @contextmanager
    def count_exception_assertions(self):
        """Count completed raises checks, never exception values or unused objects.

        The pinned pytest implementation uses this context type for both public
        raises forms. A check succeeds only when its original __exit__ accepts
        the exception, including type, match and optional check predicates.
        """
        context_type = type(pytest.raises(BaseException))
        original_enter = context_type.__enter__
        original_exit = context_type.__exit__
        entered_in_call = WeakKeyDictionary()

        def enter(context):
            result = original_enter(context)
            entered_in_call[context] = self.in_call
            return result

        def leave(context, exception_type, exception, traceback):
            eligible = entered_in_call.pop(context, False)
            accepted = original_exit(context, exception_type, exception, traceback)
            if accepted is True and eligible and self.in_call:
                self.assertions += 1
            return accepted

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(context_type, "__enter__", enter)
            patch.setattr(context_type, "__exit__", leave)
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.in_call = True
        try:
            yield
        finally:
            self.in_call = False

    def pytest_collection_modifyitems(self, items):
        self.collected = [item.nodeid for item in items]
        if self.collected != [self.nodeid]:
            self.reasons.add("case_inventory_mismatch")
            import pytest

            raise pytest.UsageError("case_inventory_mismatch")

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.reasons.add("collection_failed")

    def pytest_assertion_pass(self, item, lineno, orig, expl):
        # orig/expl may contain credentials or submitted content. Never retain them.
        if item.nodeid == self.nodeid and self.in_call:
            self.assertions += 1
        elif item.nodeid != self.nodeid:
            self.reasons.add("unexpected_case")

    def pytest_runtest_logreport(self, report):
        if report.nodeid != self.nodeid:
            self.reasons.add("unexpected_case")
            return
        self.duration_ms += report.duration * 1000
        self.phases[report.when] += 1
        if report.skipped:
            self.reasons.add("required_case_skipped")
        if report.failed:
            self.reasons.add("case_" + report.when + "_failed")
            # Only this exact metadata marker survives; never retain arbitrary
            # pytest property values, failure messages, or exception content.
            if ("evolution_environment_failure", "clock_discontinuity") in getattr(
                report, "user_properties", ()
            ):
                self.reasons.add("environment_clock_discontinuity")
        if hasattr(report, "wasxfail"):
            self.reasons.add("required_case_xfailed")

    def result(self, exit_code: int):
        if self.collected != [self.nodeid]:
            self.reasons.add("case_missing")
        if self.phases != Counter({"setup": 1, "call": 1, "teardown": 1}):
            self.reasons.add("execution_incomplete")
        if not self.assertions:
            self.reasons.add("assertions_missing")
        if exit_code:
            self.reasons.add("pytest_nonzero_exit")
        return {
            "nodeid": self.nodeid,
            "status": "fail" if self.reasons else "pass",
            "duration_ms": round(self.duration_ms, 3),
            "assertions": self.assertions,
            "reason_codes": sorted(self.reasons),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nodeid")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-catalog", required=True, type=Path)
    args = parser.parse_args(argv)
    plugin = CasePlugin(args.nodeid, read_catalog(args.source_catalog, Path.cwd()))
    with plugin.count_exception_assertions():
        status = pytest.main(
            [
                args.nodeid,
                "-q",
                "--tb=no",
                "--no-header",
                "-p",
                "no:cacheprovider",
                "-o",
                "enable_assertion_pass_hook=true",
                "-o",
                "addopts=",
            ],
            # The gate disables entry-point autoload. Load only the explicitly
            # approved, hash-locked async runner alongside our evidence hooks.
            plugins=[plugin, pytest_asyncio.plugin],
        )
    result = plugin.result(int(status))
    if plugin.failures:
        write_diagnostic(sidecar(args.destination), plugin.failures, plugin.catalog)
    # O_EXCL prevents accidental overwrite even if called with a reused path.
    with args.destination.open("x") as output:
        json.dump(result, output, sort_keys=True)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
