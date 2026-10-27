"""Apply the installed plan checker to an implementation with recorded sessions."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

GENERATION_COUNTER_MESSAGE = (
    "Current Status must start at 'Sessions completed: 0' in a freshly generated plan"
)
SESSION_SECTIONS = (
    "Work Completed",
    "Gaps Closed",
    "Decisions Made",
    "Issues / Blockers",
    "Status at End of Session",
)


def session_findings(plan):
    text = (plan / "progress.md").read_text()
    values = re.findall(r"^\*\*Sessions completed:\*\*\s*(\d+)\s*$", text, re.MULTILINE)
    if len(values) != 1:
        return ["Expected one nonnegative completed-session count"]
    completed = int(values[0])
    logs = sorted((plan / "sessions").glob("*.md"))
    if [path.name for path in logs] != [
        f"{number:03d}.md" for number in range(1, len(logs) + 1)
    ]:
        return ["Session logs must be sequential numbered files starting at 001.md"]
    if not completed <= len(logs) <= completed + 1:
        return [
            "Completed-session count disagrees with logs; at most one current session may be open"
        ]
    findings = []
    for path in logs:
        headings = set(re.findall(r"^##\s+(.+?)\s*$", path.read_text(), re.MULTILINE))
        if not set(SESSION_SECTIONS).issubset(headings):
            findings.append(f"{path.name} is missing required session sections")
    return findings


def validate(plan, checker):
    spec = importlib.util.spec_from_file_location(
        "evolution_installed_plan_checker", checker
    )
    if spec is None or spec.loader is None:
        raise ValueError("Installed checker cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    sessions = session_findings(plan)
    findings = []
    for item in module.validate(plan):
        # The original rule explicitly applies only to a freshly generated plan.
        # A live plan instead proves its counter against numbered session history.
        if (
            not sessions
            and item.code == "E007"
            and item.document == "progress.md"
            and item.message == GENERATION_COUNTER_MESSAGE
        ):
            continue
        findings.append(item.render())
    return findings + ["SESSION_HISTORY " + value for value in sessions]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument(
        "--checker",
        type=Path,
        required=True,
        help="Installed implementation-plan check_plan.py",
    )
    args = parser.parse_args(argv)
    try:
        findings = validate(args.plan, args.checker)
    except OSError, ValueError:
        print("Plan validation inputs unavailable", file=sys.stderr)
        return 2
    for value in findings:
        print(value)
    if not findings:
        print(
            "Active plan conforms; session history verified; all other installed checks retained"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
