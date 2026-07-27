"""Delivery model workbook — effort per role across the five milestones.

Audience: delivery lead, PMO, engineering management.

The whole design turns on one fact: solution-docs contains no effort, duration,
headcount or budget figures and deliberately refuses to. So this workbook is a
model, not a report. Every number a reader could act on is either an assumption
they can see and edit (Assumptions sheet) or a formula over those assumptions.
There is exactly one sheet with typed-in numbers, and it is labelled as such.

Sheet order is the reading order: what this is, what you own, the work, the
rollups, then the context the rollups sit in.
"""

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import facts as f
from lib import (
    BODY_MUTED, CENTRE, DAYS_FMT, MONEY_FMT, PCT_FMT, WARN_FONT, cell, data_row,
    header_row, legend, note, section, sheet, title,
)

# --- Assumptions sheet anchors --------------------------------------------
# Every formula in the workbook points here, so the layout is fixed in one place.
SIZE_ROW0 = 8                                   # first size row
SIZE_ROW1 = SIZE_ROW0 + len(f.SIZES) - 1
ROLE_ROW0 = SIZE_ROW1 + 4                       # first role row
ROLE_ROW1 = ROLE_ROW0 + len(f.ROLES) - 1
CONTINGENCY = "Assumptions!$C$" + str(ROLE_ROW1 + 3)
SIZE_TABLE = f"Assumptions!$A${SIZE_ROW0}:$C${SIZE_ROW1}"
RATE_RANGE = f"Assumptions!$C${ROLE_ROW0}:$C${ROLE_ROW1}"


def _rate(i):
    return f"Assumptions!$C${ROLE_ROW0 + i}"


# --- Work items sheet columns ----------------------------------------------
WI_ROW0 = 6                                     # first data row
COL_SIZE = 5                                    # E
COL_BASE = 6                                    # F
COL_DAYS = 7                                    # G — days including contingency
COL_SHARE0 = 8                                  # H..K
COL_SHARE_SUM = COL_SHARE0 + len(f.ROLES)       # L
COL_ROLE0 = COL_SHARE_SUM + 1                   # M..P
WI_ROW1 = WI_ROW0 + len(f.WORK_ITEMS) - 1


def _wi_range(col):
    letter = get_column_letter(col)
    return f"'Work items'!${letter}${WI_ROW0}:${letter}${WI_ROW1}"


def read_me(wb):
    ws = sheet(wb, "Read me", [22, 30, 30, 22, 22, 22])
    row = title(
        ws, 1, "AI Guardrails — delivery model",
        "Effort per role across the five delivery milestones. Generated from "
        "solution-docs; the effort model on top of it is an assumption, not an "
        "estimate the programme has produced.")

    row = section(ws, row, "What this workbook is")
    row = note(ws, row,
               "The milestones, their completion criteria, the dependencies and the "
               "release gates are transcribed from the delivery documentation. The "
               "sizes and role splits applied to them are not — nothing in the source "
               "material supplies effort, duration, headcount or budget figures, and "
               "it says so deliberately in more than one place. Treating any total in "
               "this workbook as a programme estimate would be a category error.",
               height=58)
    row += 1

    row = section(ws, row, "How to use it")
    for i, text in enumerate([
        "1.  Open Assumptions. It is the only sheet with typed-in numbers, and every "
        "one of them is a placeholder chosen to make the model run — not a figure "
        "read from anywhere. Replace them.",
        "2.  Set a day rate per role if you want cost. Leave the rates empty and every "
        "cost column stays empty rather than showing a misleading zero.",
        "3.  Adjust sizes and role splits on Work items where you disagree. Both are "
        "judgements, and the reasoning behind each is recorded in the generator so it "
        "can be argued with rather than guessed at.",
        "4.  Read the rollups. Effort by role and Effort by milestone are formulas "
        "over what you set — they hold no numbers of their own.",
    ]):
        row = note(ws, row, text, height=30)
    row += 1

    row = section(ws, row, "What this workbook does not say")
    for text in f.STANDING_CAVEATS:
        c = ws.cell(row=row, column=1, value=text)
        c.font = BODY_MUTED
        c.alignment = ws.cell(row=row, column=1).alignment.copy(wrap_text=True,
                                                               vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws.row_dimensions[row].height = 44
        row += 1
    row += 1

    row = legend(ws, row)

    row = section(ws, row, "Where the derived content comes from")
    row = header_row(ws, row, ["Sheet", "Source", "", "", "", ""])
    for name, src in [
        ("Work items", "MILESTONES.md §3–§7 completion criteria, in document order"),
        ("Roadmap", "MILESTONES.md §2–§7; AGR-020 five-phase delivery"),
        ("Dependencies", "DEPENDENCIES.md §2 dependency register and §3 detail"),
        ("Release gates", "RELEASE-CRITERIA.md gate tables"),
        ("Roles on every sheet", "TEAM-AND-OWNERSHIP.md §1 four ownership groups"),
        ("Sizes and role splits", "Nowhere — assumed here. See Assumptions."),
    ]:
        cell(ws, row, 1, name, bold=True)
        c = ws.cell(row=row, column=2, value=src)
        c.font = BODY_MUTED
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        row += 1
    ws.freeze_panes = "A1"
    return ws


def assumptions(wb):
    ws = sheet(wb, "Assumptions", [14, 26, 16, 62, 20, 20])
    row = title(
        ws, 1, "Assumptions — the only sheet you edit",
        "Everything else in this workbook is either transcribed from the "
        "documentation or computed from what is here.")

    c = ws.cell(row=row, column=1,
                value="The person-day values below are PLACEHOLDERS. No effort "
                      "estimate exists in the source material — these exist so the "
                      "model runs, and they must be replaced before any total is "
                      "quoted to anyone.")
    c.font = WARN_FONT
    c.alignment = c.alignment.copy(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.row_dimensions[row].height = 42

    row = header_row(ws, SIZE_ROW0 - 1,
                     ["Size", "Label", "Person-days", "What it means", "", ""])
    for key, label, meaning, days in f.SIZES:
        cell(ws, row, 1, key, bold=True, align=CENTRE)
        cell(ws, row, 2, label)
        cell(ws, row, 3, days, kind="input", fmt=DAYS_FMT, align=CENTRE)
        cell(ws, row, 4, meaning)
        row += 1

    row = header_row(ws, ROLE_ROW0 - 1,
                     ["Role", "Name", "Day rate", "Mandate", "", ""])
    for key, name, mandate in f.ROLES:
        cell(ws, row, 1, key, bold=True, align=CENTRE)
        cell(ws, row, 2, name)
        cell(ws, row, 3, None, kind="input", fmt=MONEY_FMT, align=CENTRE)
        cell(ws, row, 4, mandate)
        row += 1

    row = ROLE_ROW1 + 3
    cell(ws, row, 1, "Contingency", bold=True)
    cell(ws, row, 2, "Applied to every work item")
    cell(ws, row, 3, 0, kind="input", fmt=PCT_FMT, align=CENTRE)
    cell(ws, row, 4, "A single uplift across the whole model. Zero by default — a "
                     "contingency figure is a judgement, and inventing one on your "
                     "behalf would defeat the point of this sheet.")
    row += 2

    note(ws, row,
         "Day rates are intentionally empty. With no rate set, every cost column "
         "elsewhere in the workbook stays empty rather than computing a zero that "
         "could be mistaken for a real figure.")
    return ws


def work_items(wb):
    widths = [10, 11, 15, 62, 8, 11, 11] + [9] * len(f.ROLES) + [10] + \
             [10] * len(f.ROLES)
    ws = sheet(wb, "Work items", widths)
    title(ws, 1, "Work items",
          "One row per milestone completion criterion, in document order. Size and "
          "role split are assumptions — edit them. Everything shaded blue is a "
          "formula.", span=len(widths))

    role_names = [key for key, _, _ in f.ROLES]
    header_row(ws, WI_ROW0 - 1,
               ["ID", "Milestone", "Workstream", "Work item", "Size", "Base days",
                "Days incl. cont."]
               + [f"{k} share" for k in role_names]
               + ["Share Σ"]
               + [f"{k} days" for k in role_names])

    validation = DataValidation(
        type="list",
        formula1='"' + ",".join(key for key, _, _, _ in f.SIZES) + '"',
        allow_blank=False)
    ws.add_data_validation(validation)

    row = WI_ROW0
    for wid, milestone, workstream, item, size, shares in f.WORK_ITEMS:
        band = (row - WI_ROW0) % 2 == 1
        cell(ws, row, 1, wid, bold=True, band=band)
        cell(ws, row, 2, milestone, align=CENTRE, band=band)
        cell(ws, row, 3, workstream, band=band)
        cell(ws, row, 4, item, band=band)

        size_cell = cell(ws, row, COL_SIZE, size, kind="input", align=CENTRE)
        validation.add(size_cell)

        col_size = get_column_letter(COL_SIZE)
        cell(ws, row, COL_BASE,
             f"=VLOOKUP(${col_size}{row},{SIZE_TABLE},3,FALSE)",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)
        col_base = get_column_letter(COL_BASE)
        cell(ws, row, COL_DAYS,
             f"=${col_base}{row}*(1+{CONTINGENCY})",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)

        for i, key in enumerate(role_names):
            cell(ws, row, COL_SHARE0 + i, shares.get(key, 0), kind="input",
                 fmt=PCT_FMT, align=CENTRE)

        first = get_column_letter(COL_SHARE0)
        last = get_column_letter(COL_SHARE0 + len(role_names) - 1)
        cell(ws, row, COL_SHARE_SUM, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=PCT_FMT, align=CENTRE)

        col_days = get_column_letter(COL_DAYS)
        for i in range(len(role_names)):
            share_col = get_column_letter(COL_SHARE0 + i)
            cell(ws, row, COL_ROLE0 + i,
                 f"=${col_days}{row}*{share_col}{row}",
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 4, "Total", bold=True, align=CENTRE)
    cell(ws, row, COL_DAYS,
         f"=SUM({get_column_letter(COL_DAYS)}{WI_ROW0}:"
         f"{get_column_letter(COL_DAYS)}{WI_ROW1})",
         kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
    for i in range(len(role_names)):
        letter = get_column_letter(COL_ROLE0 + i)
        cell(ws, row, COL_ROLE0 + i,
             f"=SUM({letter}{WI_ROW0}:{letter}{WI_ROW1})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
    return ws


def effort_by_role(wb):
    milestones = [m[0] for m in f.MILESTONES]
    widths = [10, 26] + [11] * len(milestones) + [13, 13, 15]
    ws = sheet(wb, "Effort by role", widths)
    title(ws, 1, "Effort by role",
          "Every cell is a formula over Work items and Assumptions. Nothing here "
          "holds a number of its own.", span=len(widths))

    hdr = 4
    header_row(ws, hdr, ["Role", "Name"] + milestones
               + ["Total days", "Day rate", "Cost"])

    row = hdr + 1
    for i, (key, name, _) in enumerate(f.ROLES):
        band = i % 2 == 1
        cell(ws, row, 1, key, bold=True, align=CENTRE, band=band)
        cell(ws, row, 2, name, band=band)
        for j, milestone in enumerate(milestones):
            cell(ws, row, 3 + j,
                 f'=SUMIF({_wi_range(2)},"{milestone}",{_wi_range(COL_ROLE0 + i)})',
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        first = get_column_letter(3)
        last = get_column_letter(2 + len(milestones))
        total_col = 3 + len(milestones)
        cell(ws, row, total_col, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
        cell(ws, row, total_col + 1, f"={_rate(i)}", kind="computed",
             fmt=MONEY_FMT, align=CENTRE)
        cell(ws, row, total_col + 2,
             f'=IF({_rate(i)}="","",{get_column_letter(total_col)}{row}*{_rate(i)})',
             kind="computed", fmt=MONEY_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 2, "Total", bold=True)
    for j in range(len(milestones) + 1):
        letter = get_column_letter(3 + j)
        cell(ws, row, 3 + j, f"=SUM({letter}{hdr + 1}:{letter}{row - 1})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
    cost_col = get_column_letter(5 + len(milestones))
    cell(ws, row, 5 + len(milestones),
         f'=IF(COUNT({RATE_RANGE})=0,"",SUM({cost_col}{hdr + 1}:{cost_col}{row - 1}))',
         kind="computed", fmt=MONEY_FMT, bold=True, align=CENTRE)

    note(ws, row + 2,
         "Cost stays empty until a day rate is set on the Assumptions sheet. An "
         "empty cost column is the honest state of this model today.",
         span=len(widths))
    return ws


def effort_by_milestone(wb):
    role_names = [key for key, _, _ in f.ROLES]
    widths = [10, 28, 11] + [11] * len(role_names) + [13, 13, 15]
    ws = sheet(wb, "Effort by milestone", widths)
    title(ws, 1, "Effort by milestone",
          "The same model, rolled up the other way. Milestone order is dependency "
          "sequencing — it carries no dates and implies no schedule.",
          span=len(widths))

    hdr = 4
    header_row(ws, hdr, ["Milestone", "Name", "Phase"]
               + [f"{k} days" for k in role_names]
               + ["Total days", "Work items", "Cost"])

    role0 = 4
    row = hdr + 1
    for i, (key, name, phase, _mode, _obj, _entry) in enumerate(f.MILESTONES):
        band = i % 2 == 1
        cell(ws, row, 1, key, bold=True, align=CENTRE, band=band)
        cell(ws, row, 2, name, band=band)
        cell(ws, row, 3, phase, align=CENTRE, band=band)
        for j in range(len(role_names)):
            cell(ws, row, role0 + j,
                 f'=SUMIF({_wi_range(2)},$A{row},{_wi_range(COL_ROLE0 + j)})',
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        first = get_column_letter(role0)
        last = get_column_letter(role0 + len(role_names) - 1)
        total_col = role0 + len(role_names)
        cell(ws, row, total_col, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
        cell(ws, row, total_col + 1, f'=COUNTIF({_wi_range(2)},$A{row})',
             kind="computed", align=CENTRE)
        terms = " + ".join(
            f"{get_column_letter(role0 + j)}{row}*{_rate(j)}"
            for j in range(len(role_names)))
        cell(ws, row, total_col + 2,
             f'=IF(COUNT({RATE_RANGE})=0,"",{terms})',
             kind="computed", fmt=MONEY_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 2, "Total", bold=True)
    for j in range(len(role_names) + 2):
        letter = get_column_letter(role0 + j)
        fmt = None if j == len(role_names) + 1 else DAYS_FMT
        cell(ws, row, role0 + j, f"=SUM({letter}{hdr + 1}:{letter}{row - 1})",
             kind="computed", fmt=fmt, bold=True, align=CENTRE)
    cost_col = get_column_letter(role0 + len(role_names) + 2)
    cell(ws, row, role0 + len(role_names) + 2,
         f'=IF(COUNT({RATE_RANGE})=0,"",SUM({cost_col}{hdr + 1}:{cost_col}{row - 1}))',
         kind="computed", fmt=MONEY_FMT, bold=True, align=CENTRE)

    note(ws, row + 2,
         "Phases 3, 4 and 5 lie beyond the Phase 1–2 validation gate and must not be "
         "resourced ahead of it. Their totals appear because the feature set is "
         "defined and estimated across all five phases — not because the work may "
         "start.", span=len(widths))
    return ws


def roadmap(wb):
    widths = [10, 28, 11, 34, 52, 52]
    ws = sheet(wb, "Roadmap", widths)
    title(ws, 1, "Roadmap",
          "Dependency sequencing, not scheduling. No calendar dates, quarters or "
          "durations exist anywhere in the source material and none are invented "
          "here.", span=len(widths))

    row = header_row(ws, 4, ["Milestone", "Name", "Phase", "Primary deployment mode",
                             "Objective", "Entry condition"])
    for i, (key, name, phase, mode, objective, entry) in enumerate(f.MILESTONES):
        row = data_row(ws, row, [key, name, phase, mode, objective, entry],
                       band=i % 2 == 1)
    ws.cell(row=row - len(f.MILESTONES), column=1)

    row += 1
    row = section(ws, row, "The gate that matters", span=len(widths))
    note(ws, row,
         "The full Phase 1–2 validation demonstration — all nine criteria together, "
         "against two genuinely independent deployments — passes at M2, not M1. Only "
         "one criterion, policy upgrade and rollback, is undemonstrable until M2, and "
         "no subset satisfies the gate. M3 and M4 lie beyond it.",
         span=len(widths), height=44)
    return ws


def dependencies(wb):
    widths = [42, 12, 34, 30, 18, 60]
    ws = sheet(wb, "Dependencies", widths)
    title(ws, 1, "Dependencies",
          "What has to be true before the work can proceed, and who owns it. Status "
          "is as recorded in the delivery documentation — none of these is confirmed "
          "today.", span=len(widths))

    row = header_row(ws, 4, ["Dependency", "Type", "Needed for", "Owner", "Status",
                             "Why it gates"])
    for i, dep in enumerate(f.DEPENDENCIES):
        row = data_row(ws, row, list(dep), band=i % 2 == 1)

    row += 1
    note(ws, row,
         "Two of these are outside the delivery team's control entirely: managed "
         "engine access in the target Regions, and a second team's willingness to run "
         "an independent instance with the connector installed. The second gates the "
         "validation demonstration and is a named, currently unvalidated assumption.",
         span=len(widths), height=44)
    return ws


def release_gates(wb):
    widths = [13, 34, 66, 40, 11]
    ws = sheet(wb, "Release gates", widths)
    title(ws, 1, "Release gates",
          "Pass or fail statements with a named owner. The nine validation gates must "
          "pass together — a subset does not satisfy the demonstration, and one "
          "deployment exercised twice does not either.", span=len(widths))

    row = header_row(ws, 4, ["ID", "Gate", "What passing means", "Accountable owner",
                             "Milestone"])
    for i, gate in enumerate(f.RELEASE_GATES):
        gid, name, statement, owner, milestone = gate
        row = data_row(ws, row, [gid, name, statement, owner, milestone],
                       band=i % 2 == 1)

    row += 1
    note(ws, row,
         "Verification method, environment and sign-off owner per criterion are only "
         "partly assigned in the delivery plan — a gap owed before the first phase "
         "exits. Where this sheet names an owner, that owner is recorded upstream; it "
         "is not an allocation made here.",
         span=len(widths), height=44)
    return ws


SHEETS = [
    read_me,
    assumptions,
    work_items,
    effort_by_role,
    effort_by_milestone,
    roadmap,
    dependencies,
    release_gates,
]
