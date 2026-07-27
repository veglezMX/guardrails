"""Verify a built workbook without opening Excel.

    python3 tools/xlsx/verify.py <book-name | path.xlsx>
    python3 tools/xlsx/verify.py --all

Checks, in order:

1. Structure — every sheet the book module declares is present.
2. Model integrity — role shares sum to 1 on every work item; ids are unique;
   every milestone and size referenced is one that exists; every milestone has
   work.
3. Formula discipline — the computed columns really are formulas. A typed-in
   number in a rollup is the failure this workbook exists to prevent, because it
   would look identical to a computed one.
4. Anchor drift — the ranges the formulas point at still line up with where the
   Assumptions sheet actually puts its size and rate tables. A silent
   one-row shift here would make every total wrong and nothing would look broken.
5. Scheduling language — no calendar dates, quarters, or durations anywhere. The
   source material fixes milestone order and deliberately says nothing about
   when; a workbook is where that discipline usually breaks first.
6. Cost neutrality — day rates ship empty, so no cost figure is asserted.
   Reported as a notice, not a failure: filling them in is the intended use.

Run via `just xlsx-check <name>`.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

import facts as f
from books import delivery as d
from officemeta import audit
from registry import BOOKS, resolve

# Scheduling language the delivery documentation refuses to state, and which a
# spreadsheet invites someone to add.
SCHEDULE_PATTERNS = [
    r"\bQ[1-4]\b",
    r"\b20\d{2}-\d{2}-\d{2}\b",
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\b",
    r"\b\d+\s*(week|month|quarter|sprint)s?\b",
    r"\bby\s+(mid|end|early)[- ]20\d{2}\b",
]

ROLE_COUNT = len(f.ROLES)


def _cells(ws):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                yield cell


def check(path, label=None):
    wb = load_workbook(str(path))
    failures, notices = list(audit(path)), []

    # --- 1. structure ------------------------------------------------------
    expected = [fn.__name__.replace("_", " ") for fn in d.SHEETS]
    missing = [name for name in ("Read me", "Assumptions", "Work items",
                                 "Effort by role", "Effort by milestone",
                                 "Roadmap", "Dependencies", "Release gates")
               if name not in wb.sheetnames]
    if missing:
        failures.append(f"missing sheet(s): {', '.join(missing)}")
    del expected

    # --- 2. model integrity ------------------------------------------------
    size_keys = {key for key, _, _, _ in f.SIZES}
    milestone_keys = {m[0] for m in f.MILESTONES}
    role_keys = set(f.ROLE_KEYS)
    seen_ids, worked_milestones = set(), set()

    for wid, milestone, _ws_name, _item, size, shares in f.WORK_ITEMS:
        if wid in seen_ids:
            failures.append(f"{wid}: duplicate work-item id")
        seen_ids.add(wid)
        worked_milestones.add(milestone)
        if milestone not in milestone_keys:
            failures.append(f"{wid}: unknown milestone {milestone!r}")
        if size not in size_keys:
            failures.append(f"{wid}: unknown size {size!r}")
        unknown = set(shares) - role_keys
        if unknown:
            failures.append(f"{wid}: unknown role(s) {', '.join(sorted(unknown))}")
        total = round(sum(shares.values()), 6)
        if total != 1.0:
            failures.append(f"{wid}: role shares sum to {total}, expected 1.0")

    for milestone in sorted(milestone_keys - worked_milestones):
        failures.append(f"{milestone}: milestone has no work items")

    # --- 3. formula discipline --------------------------------------------
    ws = wb["Work items"]
    computed_cols = [d.COL_BASE, d.COL_DAYS, d.COL_SHARE_SUM] + \
                    [d.COL_ROLE0 + i for i in range(ROLE_COUNT)]
    for row in range(d.WI_ROW0, d.WI_ROW1 + 1):
        for col in computed_cols:
            value = ws.cell(row=row, column=col).value
            if not (isinstance(value, str) and value.startswith("=")):
                failures.append(
                    f"Work items r{row}c{col}: expected a formula, found {value!r}")

    for name in ("Effort by role", "Effort by milestone"):
        rollup = wb[name]
        typed = [c.coordinate for c in _cells(rollup)
                 if isinstance(c.value, (int, float))]
        if typed:
            failures.append(
                f"{name}: typed-in number(s) in a rollup sheet at "
                f"{', '.join(typed[:6])} — rollups must be formulas only")

    # --- 4. anchor drift ---------------------------------------------------
    assumptions = wb["Assumptions"]
    found_sizes = [r for r in range(1, assumptions.max_row + 1)
                   if assumptions.cell(row=r, column=1).value in size_keys]
    if found_sizes and (min(found_sizes), max(found_sizes)) != (d.SIZE_ROW0,
                                                               d.SIZE_ROW1):
        failures.append(
            f"Assumptions: size table sits at rows {min(found_sizes)}–"
            f"{max(found_sizes)} but formulas point at {d.SIZE_ROW0}–{d.SIZE_ROW1}")
    found_roles = [r for r in range(1, assumptions.max_row + 1)
                   if assumptions.cell(row=r, column=1).value in role_keys]
    if found_roles and (min(found_roles), max(found_roles)) != (d.ROLE_ROW0,
                                                               d.ROLE_ROW1):
        failures.append(
            f"Assumptions: rate table sits at rows {min(found_roles)}–"
            f"{max(found_roles)} but formulas point at {d.ROLE_ROW0}–{d.ROLE_ROW1}")

    # --- 4b. rollup references, and a shadow calculation -------------------
    # openpyxl does not evaluate formulas and no spreadsheet engine is assumed
    # here, so instead: confirm every SUMIF points at the work-item row span and
    # the correct role column, then recompute the model independently and report
    # the totals. A drifted column reference would otherwise total silently to
    # zero and look entirely healthy.
    sumif = re.compile(
        r"SUMIF\('Work items'!\$(?P<key>[A-Z]+)\$(?P<kr0>\d+):\$[A-Z]+\$(?P<kr1>\d+),"
        r".*?,'Work items'!\$(?P<sum>[A-Z]+)\$(?P<sr0>\d+):\$[A-Z]+\$(?P<sr1>\d+)\)")
    role_cols = {get_column_letter(d.COL_ROLE0 + i) for i in range(ROLE_COUNT)}
    key_col = get_column_letter(2)
    seen_sumifs = 0
    for name in ("Effort by role", "Effort by milestone"):
        for cell in _cells(wb[name]):
            if not (isinstance(cell.value, str) and "SUMIF(" in cell.value):
                continue
            m = sumif.search(cell.value)
            if not m:
                failures.append(f"{name}!{cell.coordinate}: unparseable SUMIF")
                continue
            seen_sumifs += 1
            if m.group("key") != key_col:
                failures.append(
                    f"{name}!{cell.coordinate}: milestone criteria column is "
                    f"{m.group('key')}, expected {key_col}")
            if m.group("sum") not in role_cols:
                failures.append(
                    f"{name}!{cell.coordinate}: sums column {m.group('sum')}, which is "
                    f"not one of the role-day columns {sorted(role_cols)}")
            spans = {(int(m.group("kr0")), int(m.group("kr1"))),
                     (int(m.group("sr0")), int(m.group("sr1")))}
            if spans != {(d.WI_ROW0, d.WI_ROW1)}:
                failures.append(
                    f"{name}!{cell.coordinate}: ranges cover {sorted(spans)}, expected "
                    f"rows {d.WI_ROW0}–{d.WI_ROW1}")
    if seen_sumifs != ROLE_COUNT * len(f.MILESTONES) * 2:
        failures.append(
            f"expected {ROLE_COUNT * len(f.MILESTONES) * 2} rollup SUMIFs, found "
            f"{seen_sumifs}")

    days_for = {assumptions.cell(row=r, column=1).value:
                assumptions.cell(row=r, column=3).value
                for r in range(d.SIZE_ROW0, d.SIZE_ROW1 + 1)}
    shadow = {(m[0], k): 0.0 for m in f.MILESTONES for k in f.ROLE_KEYS}
    if all(isinstance(v, (int, float)) for v in days_for.values()):
        for _wid, milestone, _wsn, _it, size, shares in f.WORK_ITEMS:
            for key, share in shares.items():
                shadow[(milestone, key)] += days_for[size] * share
    else:
        notices.append("Assumptions: size table is not fully populated, so the shadow "
                       "calculation was skipped")

    # --- 5. scheduling language -------------------------------------------
    for sheet_name in wb.sheetnames:
        for cell in _cells(wb[sheet_name]):
            if not isinstance(cell.value, str) or cell.value.startswith("="):
                continue
            for pattern in SCHEDULE_PATTERNS:
                for m in re.finditer(pattern, cell.value):
                    start = max(0, m.start() - 50)
                    excerpt = f"…{cell.value[start:m.end() + 50]}…".replace("\n", " ")
                    failures.append(
                        f"{sheet_name}!{cell.coordinate}: scheduling language "
                        f"/{pattern}/ — {excerpt}")

    # --- 6. cost neutrality ------------------------------------------------
    rates = [assumptions.cell(row=r, column=3).value
             for r in range(d.ROLE_ROW0, d.ROLE_ROW1 + 1)]
    if any(r is not None for r in rates):
        notices.append("Assumptions: a day rate ships filled in — confirm the figure "
                       "is one you can defend, since cost columns will now compute")

    placeholder = [assumptions.cell(row=r, column=3).value
                   for r in range(d.SIZE_ROW0, d.SIZE_ROW1 + 1)]
    if all(v is not None for v in placeholder):
        notices.append(f"Assumptions: size table ships with placeholder person-days "
                       f"{placeholder} — these are not an estimate and must be "
                       f"replaced before any total is quoted")

    header = f"{label}: " if label else ""
    print(f"{header}{len(wb.sheetnames)} sheets · {len(f.WORK_ITEMS)} work items · "
          f"{len(f.MILESTONES)} milestones · {ROLE_COUNT} roles")
    if notices:
        print(f"\n{len(notices)} notice(s):")
        for n in notices:
            print("  ·", n)
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for failure in failures[:40]:
            print("  ✗", failure)
        if len(failures) > 40:
            print(f"  … and {len(failures) - 40} more")
        return 1
    if any(shadow.values()):
        print("\nshadow calculation from the placeholder assumptions "
              "(person-days, contingency excluded):")
        head = "  ".join(f"{m[0]:>7}" for m in f.MILESTONES)
        print(f"  {'role':<6}{head}{'total':>9}")
        for key in f.ROLE_KEYS:
            per = [shadow[(m[0], key)] for m in f.MILESTONES]
            print(f"  {key:<6}" + "  ".join(f"{v:>7.1f}" for v in per)
                  + f"{sum(per):>9.1f}")
        per_m = [sum(shadow[(m[0], k)] for k in f.ROLE_KEYS) for m in f.MILESTONES]
        print(f"  {'all':<6}" + "  ".join(f"{v:>7.1f}" for v in per_m)
              + f"{sum(per_m):>9.1f}")

    print("\nno failures: structure, model integrity, formula discipline, rollup "
          "references, anchors and scheduling language all clean")
    return 0


def check_book(spec):
    if not spec.path.exists():
        print(f"✗ {spec.name}: {spec.path} not built — run `just xlsx {spec.name}`")
        return 1
    return check(spec.path, label=spec.name)


def main(argv):
    if not argv:
        raise SystemExit(f"usage: verify.py <{' | '.join(BOOKS)} | --all | path.xlsx>")
    if argv[0] == "--all":
        worst = 0
        for spec in BOOKS.values():
            worst |= check_book(spec)
            print()
        return worst
    if argv[0] in BOOKS:
        return check_book(resolve(argv[0]))
    return check(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
