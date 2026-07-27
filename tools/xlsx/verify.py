"""Verify a built workbook without opening Excel.

    python3 tools/xlsx/verify.py <book-name | path.xlsx>
    python3 tools/xlsx/verify.py --all

Checks, in order:

1. Structure — every sheet the book module declares is present, in order.
2. Model integrity — role shares sum to 1 on every work item; ids are unique;
   every milestone and size referenced is one that exists; every milestone has
   work; the AI-assistance factors line up with the roles and are fractions.
3. Formula discipline — the computed columns really are formulas. A typed-in
   number in a rollup is the failure this workbook exists to prevent, because it
   would look identical to a computed one.
4. Anchor drift — the ranges the formulas point at still line up with where the
   Assumptions sheet actually puts its size, rate and AI-assist tables. A silent
   one-row shift here would make every total wrong and nothing would look broken.
5. Guarded references — a formula that reads an empty input cell directly
   renders as 0, not as blank. The day-rate column promises the opposite, so
   every reference to a rate must be wrapped in a blank test.
6. Enforced input — the size dropdown must reject an unknown size. Without
   showErrorMessage the list is decorative, and one bad size drives #N/A through
   every total on every sheet.
7. Scheduling language — no calendar dates, quarters, or durations anywhere. The
   source material fixes milestone order and deliberately says nothing about
   when; a workbook is where that discipline usually breaks first.
8. Cost neutrality — day rates ship empty, so no cost figure is asserted.
   Reported as a notice, not a failure: filling them in is the intended use.
9. Charts — the sheets that promise a picture carry one; every chart reads cells
   on its own sheet, so it is a view of the model rather than a second copy of
   it; every series is explicitly coloured, because a series left to Excel's
   theme picks up whatever hue the theme happens to hold and the palette here is
   ordered deliberately; and no axis is deleted, which openpyxl does by default.
10. Prose budget — the workbook is for slicing numbers, and the argued-out
   version of every judgement lives in the decks. Commentary sheets are capped
   so that prose cannot creep back in a paragraph at a time. Sheets whose text
   IS the payload — transcribed criteria, gates, dependencies — are exempt.

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

# The sheets that promise a picture, and how many charts each owes. A minimum
# rather than an exact count: adding a chart is an improvement, losing one is a
# regression, and only the second should fail a build.
CHART_MINIMUMS = {
    "Dashboard": 5,
    "Effort by role": 1,
    "Effort by milestone": 1,
    "Effort by workstream": 1,
    "Sensitivity": 1,
}

# Characters of non-formula text a commentary sheet may hold. Headroom is
# deliberate — the cap catches a paragraph creeping back in, not a reworded
# sentence. Sheets absent from this map are transcription: their text is the
# data, and capping it would mean deleting source material.
PROSE_BUDGET = {
    "Read me": 3000,
    "Dashboard": 1600,
    "Assumptions": 3400,
    "Effort by role": 900,
    "Effort by milestone": 900,
    "Effort by workstream": 900,
    "Sensitivity": 900,
}


def _cells(ws):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                yield cell


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check(path, label=None):
    wb = load_workbook(str(path))
    failures, notices = list(audit(path)), []

    # --- 1. structure ------------------------------------------------------
    # Derived from the book module, so a sheet added there is checked without
    # anyone remembering to update a list here.
    expected = [fn.__name__.replace("_", " ").capitalize() for fn in d.SHEETS]
    missing = [name for name in expected if name not in wb.sheetnames]
    if missing:
        failures.append(f"missing sheet(s): {', '.join(missing)}")
        # Every check below indexes those sheets by name. Report and stop,
        # rather than trading the failure list for a KeyError traceback.
        return _report(wb, label, failures, notices, None)
    if wb.sheetnames != expected:
        failures.append(f"sheet order is {wb.sheetnames}, expected {expected}")

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

    # The AI-assist table is indexed positionally by every formula that uses it,
    # so a reordering that looks harmless would quietly apply the wrong factor
    # to the wrong role.
    if [key for key, _, _ in f.AI_ASSIST] != f.ROLE_KEYS:
        failures.append(
            f"AI_ASSIST covers {[k for k, _, _ in f.AI_ASSIST]}, but the formulas "
            f"index it positionally against ROLES {f.ROLE_KEYS}")
    for key, factor, _why in f.AI_ASSIST:
        if not _number(factor) or not 0.0 <= factor < 1.0:
            failures.append(
                f"AI assist {key}: factor {factor!r} is not a fraction in [0, 1)")

    # --- 3. formula discipline --------------------------------------------
    ws = wb["Work items"]
    computed_cols = [d.COL_BASE, d.COL_DAYS, d.COL_SHARE_SUM] + \
                    [d.COL_ROLE0 + i for i in range(ROLE_COUNT)] + [d.COL_AI_DAYS]
    for row in range(d.WI_ROW0, d.WI_ROW1 + 1):
        for col in computed_cols:
            value = ws.cell(row=row, column=col).value
            if not (isinstance(value, str) and value.startswith("=")):
                failures.append(
                    f"Work items r{row}c{col}: expected a formula, found {value!r}")

    # Every role-day column must carry that role's AI factor, and its own — an
    # off-by-one here mis-prices a whole role and totals to something plausible.
    for row in range(d.WI_ROW0, d.WI_ROW1 + 1):
        for i in range(ROLE_COUNT):
            formula = str(ws.cell(row=row, column=d.COL_ROLE0 + i).value or "")
            token = f"(1-{d._ai(i)})"
            if token not in formula:
                failures.append(
                    f"Work items r{row}c{d.COL_ROLE0 + i}: expected the AI-assist "
                    f"factor {token}, found {formula!r}")

    for name in ("Effort by role", "Effort by milestone"):
        rollup = wb[name]
        typed = [c.coordinate for c in _cells(rollup) if _number(c.value)]
        if typed:
            failures.append(
                f"{name}: typed-in number(s) in a rollup sheet at "
                f"{', '.join(typed[:6])} — rollups must be formulas only")

    # --- 4. anchor drift ---------------------------------------------------
    assumptions = wb["Assumptions"]
    found_sizes = [r for r in range(1, assumptions.max_row + 1)
                   if assumptions.cell(row=r, column=1).value in size_keys]
    if found_sizes != list(range(d.SIZE_ROW0, d.SIZE_ROW1 + 1)):
        failures.append(
            f"Assumptions: size table sits at rows {found_sizes} but formulas point "
            f"at {d.SIZE_ROW0}–{d.SIZE_ROW1}")

    # Role keys appear twice on the sheet now — the rate table and the AI-assist
    # table — and the formulas address each by a fixed row.
    found_roles = [r for r in range(1, assumptions.max_row + 1)
                   if assumptions.cell(row=r, column=1).value in role_keys]
    expected_roles = list(range(d.ROLE_ROW0, d.ROLE_ROW1 + 1)) + \
        list(range(d.AI_ROW0, d.AI_ROW1 + 1))
    if found_roles != expected_roles:
        failures.append(
            f"Assumptions: role rows are {found_roles} but formulas point at the "
            f"rate table {d.ROLE_ROW0}–{d.ROLE_ROW1} and the AI-assist table "
            f"{d.AI_ROW0}–{d.AI_ROW1}")
    if assumptions.cell(row=d.CONT_ROW, column=1).value != "Contingency":
        failures.append(
            f"Assumptions: row {d.CONT_ROW} is not the contingency row, but every "
            f"work item multiplies by {d.CONTINGENCY}")

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

    shadow = _shadow(assumptions, notices)

    # --- 5. guarded references --------------------------------------------
    # `=Assumptions!$C$15` on an empty rate renders 0.00, which is precisely the
    # figure the sheet tells the reader it will never show.
    by_role = wb["Effort by role"]
    for cell in _cells(by_role):
        formula = cell.value
        if not isinstance(formula, str) or not formula.startswith("="):
            continue
        for i in range(ROLE_COUNT):
            rate = d._rate(i)
            if rate in formula and f'IF({rate}=""' not in formula \
                    and f"COUNT({d.RATE_RANGE})" not in formula:
                failures.append(
                    f"Effort by role!{cell.coordinate}: reads the day rate {rate} "
                    f"without a blank test — an empty rate renders as 0, not blank")

    # --- 6. enforced input -------------------------------------------------
    validations = list(wb["Work items"].data_validations.dataValidation)
    if not validations:
        failures.append("Work items: the size column has no data validation")
    for dv in validations:
        if not dv.showErrorMessage:
            failures.append(
                f"Work items: validation on {dv.sqref} does not show an error — the "
                f"list is decorative and an unknown size drives #N/A through every "
                f"total")

    # --- 7. scheduling language -------------------------------------------
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

    # --- 8. cost neutrality ------------------------------------------------
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

    factors = [assumptions.cell(row=r, column=3).value
               for r in range(d.AI_ROW0, d.AI_ROW1 + 1)]
    if any(v for v in factors if _number(v)):
        notices.append(
            "Assumptions: the workbook ships assuming AI-assisted delivery "
            + ", ".join(f"{k} {v:.0%}" for (k, _, _), v in zip(f.AI_ASSIST, factors)
                        if _number(v))
            + " — an assumption made here and stated in no source document. Zero them "
              "for the unassisted model")

    # --- 9. charts ---------------------------------------------------------
    _charts(wb, failures)

    # --- 10. prose budget --------------------------------------------------
    for name, budget in PROSE_BUDGET.items():
        chars = sum(len(c.value) for c in _cells(wb[name])
                    if isinstance(c.value, str) and not c.value.startswith("="))
        if chars > budget:
            failures.append(
                f"{name}: {chars} characters of prose against a budget of {budget} "
                f"— this is a workbook, and the long-form argument belongs in the "
                f"deck. Cut it, or tabulate it.")

    return _report(wb, label, failures, notices, shadow)


def _series_ref(series):
    """The value range a series plots, however openpyxl chose to spell it."""
    source = series.val
    if source is None:
        return None
    for kind in ("numRef", "strRef"):
        holder = getattr(source, kind, None)
        if holder is not None and holder.f:
            return holder.f
    return None


def _coloured(series):
    """An explicit fill (bars) or stroke (lines) — not Excel's theme default."""
    spPr = series.graphicalProperties
    if spPr is None:
        return False
    return bool(spPr.solidFill or (spPr.ln is not None and spPr.ln.solidFill))


def _charts(wb, failures):
    for name, minimum in CHART_MINIMUMS.items():
        charts = wb[name]._charts
        if len(charts) < minimum:
            failures.append(
                f"{name}: {len(charts)} chart(s), expected at least {minimum}")
        for index, chart in enumerate(charts):
            where = f"{name} chart {index + 1}"
            if not chart.series:
                failures.append(f"{where}: no series")
            for i, series in enumerate(chart.series):
                target = _series_ref(series)
                if target is None:
                    failures.append(f"{where} series {i + 1}: plots no range")
                elif not target.startswith(f"'{name}'!"):
                    failures.append(
                        f"{where} series {i + 1}: reads {target}, which is not on "
                        f"{name} — a chart must be a view of the table beside it")
                if not _coloured(series):
                    failures.append(
                        f"{where} series {i + 1}: no explicit colour, so Excel will "
                        f"assign one from the theme and the palette order is lost")
            for axis, axis_name in ((chart.x_axis, "x"), (chart.y_axis, "y")):
                if axis.delete:
                    failures.append(f"{where}: {axis_name} axis is deleted")


def _shadow(assumptions, notices):
    """Recompute the model in plain Python, independently of the formulas.

    Contingency is excluded — it ships at zero and is the reader's to set. The
    AI-assistance factors are applied, because they ship non-zero and are the
    difference between the two totals reported.
    """
    days_for = {assumptions.cell(row=r, column=1).value:
                assumptions.cell(row=r, column=3).value
                for r in range(d.SIZE_ROW0, d.SIZE_ROW1 + 1)}
    if not all(_number(v) for v in days_for.values()):
        notices.append("Assumptions: size table is not fully populated, so the shadow "
                       "calculation was skipped")
        return None

    factor_for = {}
    for i, (key, _f, _why) in enumerate(f.AI_ASSIST):
        value = assumptions.cell(row=d.AI_ROW0 + i, column=3).value
        factor_for[key] = float(value) if _number(value) else 0.0

    raw = {(m[0], k): 0.0 for m in f.MILESTONES for k in f.ROLE_KEYS}
    net = dict(raw)
    for _wid, milestone, _wsn, _it, size, shares in f.WORK_ITEMS:
        base = float(days_for[size])
        for key, share in shares.items():
            raw[(milestone, key)] += base * share
            net[(milestone, key)] += base * share * (1.0 - factor_for[key])
    return raw, net


def _report(wb, label, failures, notices, shadow):
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

    if shadow:
        raw, net = shadow
        print("\nshadow calculation from the placeholder assumptions "
              "(person-days, contingency excluded, AI assist applied):")
        head = "  ".join(f"{m[0]:>7}" for m in f.MILESTONES)
        print(f"  {'role':<6}{head}{'total':>9}{'unassisted':>12}{'saved':>8}")
        for key in f.ROLE_KEYS:
            per = [net[(m[0], key)] for m in f.MILESTONES]
            gross = sum(raw[(m[0], key)] for m in f.MILESTONES)
            print(f"  {key:<6}" + "  ".join(f"{v:>7.1f}" for v in per)
                  + f"{sum(per):>9.1f}{gross:>12.1f}"
                  + f"{(1 - sum(per) / gross if gross else 0):>8.0%}")
        per_m = [sum(net[(m[0], k)] for k in f.ROLE_KEYS) for m in f.MILESTONES]
        gross_all = sum(raw.values())
        print(f"  {'all':<6}" + "  ".join(f"{v:>7.1f}" for v in per_m)
              + f"{sum(per_m):>9.1f}{gross_all:>12.1f}"
              + f"{(1 - sum(per_m) / gross_all if gross_all else 0):>8.0%}")

    charts = sum(len(wb[name]._charts) for name in wb.sheetnames)
    print(f"\nno failures: structure, model integrity, formula discipline, rollup "
          f"references, anchors, guarded references, enforced input, scheduling "
          f"language, {charts} charts and the prose budget all clean")
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
