"""Deterministically export assembled v2 API contracts without opening a database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from guardrails.evolution.management_api import ManagementApi, create_app

ROOT = Path(__file__).resolve().parents[2]


def export(*, check=False):
    # OpenAPI construction needs service interfaces, never live tenant state.
    service = ManagementApi(None)
    application = create_app(service=service, distributor=object())
    value = application.openapi()
    destination = ROOT / "mvp/evolution/management-openapi.json"
    expected = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if check:
        if not destination.is_file() or destination.read_text() != expected:
            raise ValueError("management_contract_stale")
    else:
        destination.write_text(expected)
    return len(value["paths"])


def export_runtime(*, check=False):
    from guardrails.evolution.runtime_api import create_app as runtime_app
    value = runtime_app(service=object()).openapi()
    destination = ROOT / "mvp/evolution/runtime-openapi.json"
    expected = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if check:
        if not destination.is_file() or destination.read_text() != expected:
            raise ValueError("runtime_contract_stale")
    else:
        destination.write_text(expected)
    return len(value["paths"])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps({"management_paths": export(check=args.check), "runtime_paths": export_runtime(check=args.check), "current": True})
        )
        return 0
    except ValueError, OSError:
        print(json.dumps({"current": False, "reason": "contract_export_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
